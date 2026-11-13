package com.btbridge

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.graphics.ImageFormat
import android.media.AudioAttributes
import android.media.AudioManager
import android.media.AudioTrack
import android.hardware.camera2.CameraAccessException
import android.hardware.camera2.CameraCaptureSession
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraDevice
import android.hardware.camera2.CameraManager
import android.hardware.camera2.CameraMetadata
import android.hardware.camera2.CaptureRequest
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.ImageReader
import android.media.MediaRecorder
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import android.os.Build
import android.os.Handler
import android.os.HandlerThread
import android.os.IBinder
import android.os.PowerManager
import android.util.Log
import android.util.Size
import org.json.JSONObject
import java.io.IOException
import java.net.Inet4Address
import java.net.InetSocketAddress
import java.net.NetworkInterface
import java.net.ServerSocket
import java.net.Socket
import java.nio.charset.StandardCharsets
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.math.abs
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt

class BridgeService : Service() {

    companion object {
        private const val TAG = "BTBridge"

        const val ACTION_START = "com.btbridge.START"
        const val ACTION_STOP = "com.btbridge.STOP"
        const val ACTION_TOGGLE = "com.btbridge.TOGGLE"

        /** Broadcast emitted whenever the service state changes; tiles/UI mirror this. */
        const val ACTION_STATE = "com.btbridge.STATE"
        const val EXTRA_RUNNING = "running"

        private const val CHANNEL_ID = "btbridge"
        private const val NOTIF_ID = 47110

        @Volatile
        var running = false
            private set

        @Volatile
        var statusText = "Off"
            private set

        @Volatile
        var listenAddress = "0.0.0.0:${Proto.PORT}"
            private set

        @Volatile
        var clientConnected = false
            private set

        fun deviceName(): String = Build.MODEL?.takeIf { it.isNotBlank() } ?: "Android"

        fun start(ctx: Context) {
            val i = Intent(ctx, BridgeService::class.java).setAction(ACTION_START)
            startCompat(ctx, i)
        }

        fun stop(ctx: Context) {
            val i = Intent(ctx, BridgeService::class.java).setAction(ACTION_STOP)
            startCompat(ctx, i)
        }

        fun toggle(ctx: Context) {
            val i = Intent(ctx, BridgeService::class.java).setAction(ACTION_TOGGLE)
            startCompat(ctx, i)
        }

        private fun startCompat(ctx: Context, i: Intent) {
            try {
                ctx.startForegroundService(i)
            } catch (t: Throwable) {
                Log.w(TAG, "startForegroundService failed: $t")
            }
        }

        internal fun stateChanged(ctx: Context) {
            val i = Intent(ACTION_STATE).setPackage(ctx.packageName)
                .putExtra(EXTRA_RUNNING, running)
            try {
                ctx.sendBroadcast(i)
            } catch (t: Throwable) {
                Log.w(TAG, "state broadcast failed: $t")
            }
        }
    }

    private var wakeLock: PowerManager.WakeLock? = null
    private var serverSocket: ServerSocket? = null
    private var acceptThread: Thread? = null
    private val clientLock = Any()

    @Volatile
    private var client: ClientSession? = null

    private var nsdManager: NsdManager? = null
    private var registrationListener: NsdManager.RegistrationListener? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        createChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> {
                if (running) {
                    stopServing()
                } else {
                    // Started with startForegroundService(): satisfy the contract, then leave.
                    startAsForeground()
                    stopForegroundCompat()
                    stopSelf()
                }
            }
            ACTION_TOGGLE -> {
                if (running) stopServing() else startServing()
            }
            else -> startServing()
        }
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        stopServing()
        super.onDestroy()
    }

    // ---------------------------------------------------------------- serving

    private fun startServing() {
        if (running) {
            updateNotification()
            return
        }
        startAsForeground()
        running = true
        clientConnected = false
        statusText = "Ready · " + networkKind()
        try {
            val pm = getSystemService(PowerManager::class.java)
            val wl = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "BTBridge::bridge")
            wl.setReferenceCounted(false)
            wl.acquire()
            wakeLock = wl
        } catch (t: Throwable) {
            Log.w(TAG, "wakelock unavailable: $t")
        }

        if (!startServer()) {
            statusText = "Port ${Proto.PORT} unavailable"
            stopServing()
            return
        }
        registerMdns()
        updateNotification()
        stateChanged(this)
    }

    private fun stopServing() {
        val wasRunning = running
        running = false
        clientConnected = false

        synchronized(clientLock) {
            try {
                client?.close()
            } catch (_: Throwable) {
            }
            client = null
        }
        try {
            serverSocket?.close()
        } catch (_: Throwable) {
        }
        serverSocket = null
        acceptThread?.let {
            try {
                it.join(500)
            } catch (_: Throwable) {
            }
        }
        acceptThread = null

        unregisterMdns()

        wakeLock?.let {
            try {
                if (it.isHeld) it.release()
            } catch (_: Throwable) {
            }
        }
        wakeLock = null

        stopForegroundCompat()
        statusText = "Off"
        if (wasRunning) stateChanged(this)
        stopSelf()
    }

    private fun startAsForeground() {
        val notif = buildNotification()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            var types = 0
            if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) {
                types = types or ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE
            }
            if (checkSelfPermission(Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED) {
                types = types or ServiceInfo.FOREGROUND_SERVICE_TYPE_CAMERA
            }
            if (types != 0) {
                try {
                    startForeground(NOTIF_ID, notif, types)
                    return
                } catch (t: Throwable) {
                    Log.w(TAG, "startForeground(types=$types) failed: $t")
                }
            }
        }
        try {
            startForeground(NOTIF_ID, notif)
        } catch (t: Throwable) {
            Log.w(TAG, "startForeground failed: $t")
        }
    }

    private fun stopForegroundCompat() {
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
                stopForeground(STOP_FOREGROUND_REMOVE)
            } else {
                @Suppress("DEPRECATION")
                stopForeground(true)
            }
        } catch (t: Throwable) {
            Log.w(TAG, "stopForeground: $t")
        }
    }

    private fun createChannel() {
        try {
            val nm = getSystemService(NotificationManager::class.java)
            val ch = NotificationChannel(
                CHANNEL_ID,
                "BTBridge service",
                NotificationManager.IMPORTANCE_LOW
            )
            ch.description = "Keeps the BTBridge listener running"
            ch.setShowBadge(false)
            nm.createNotificationChannel(ch)
        } catch (t: Throwable) {
            Log.w(TAG, "channel: $t")
        }
    }

    private fun buildNotification(): Notification {
        val open = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        val stopIntent = PendingIntent.getService(
            this, 1, Intent(this, BridgeService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        val b = Notification.Builder(this, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.stat_sys_data_bluetooth)
            .setContentTitle("BTBridge")
            .setContentText("$statusText · $listenAddress")
            .setContentIntent(open)
            .setOngoing(true)
            .addAction(
                Notification.Action.Builder(
                    null, "Stop",
                    PendingIntent.getService(
                        this, 2, Intent(this, BridgeService::class.java).setAction(ACTION_STOP),
                        PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
                    )
                ).build()
            )
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            b.setForegroundServiceBehavior(Notification.FOREGROUND_SERVICE_IMMEDIATE)
        }
        return b.build()
    }

    private fun updateNotification() {
        try {
            val nm = getSystemService(NotificationManager::class.java)
            nm.notify(NOTIF_ID, buildNotification())
        } catch (t: Throwable) {
            Log.w(TAG, "notify: $t")
        }
    }

    private fun startServer(): Boolean {
        return try {
            val ss = ServerSocket()
            ss.reuseAddress = true
            ss.bind(InetSocketAddress("0.0.0.0", Proto.PORT))
            serverSocket = ss
            listenAddress = "${localIpv4() ?: "0.0.0.0"}:${Proto.PORT}"
            acceptThread = Thread({ acceptLoop(ss) }, "btbridge-accept").also {
                it.isDaemon = true
                it.start()
            }
            true
        } catch (t: Throwable) {
            Log.w(TAG, "cannot listen on ${Proto.PORT}: $t")
            false
        }
    }

    private fun acceptLoop(ss: ServerSocket) {
        while (running) {
            val socket = try {
                ss.accept()
            } catch (t: Throwable) {
                break
            }
            // One client at a time; a new connection replaces the previous one.
            val old = synchronized(clientLock) {
                val o = client
                client = null
                o
            }
            try {
                old?.close()
            } catch (_: Throwable) {
            }

            val session = ClientSession(this.applicationContext, this, socket)
            synchronized(clientLock) { client = session }
            try {
                session.start()
            } catch (t: Throwable) {
                Log.w(TAG, "session start failed: $t")
                try {
                    session.close()
                } catch (_: Throwable) {
                }
            }
        }
    }

    internal fun onClientGone(session: ClientSession) {
        synchronized(clientLock) {
            if (client === session) client = null
        }
        clientConnected = false
        updateStatus()
        updateNotification()
        stateChanged(this)
    }

    internal fun onClientReady(session: ClientSession) {
        clientConnected = true
        updateStatus()
        updateNotification()
        stateChanged(this)
    }

    internal fun onCaptureStateChanged() {
        updateStatus()
        updateNotification()
    }

    private fun updateStatus() {
        statusText = when {
            !running -> "Off"
            !clientConnected -> "Ready · ${networkKind()} · waiting for PC"
            else -> {
                val s = synchronized(clientLock) { client }
                val parts = ArrayList<String>(2)
                if (s?.micActive == true) parts.add("mic")
                if (s?.camActive == true) parts.add("cam")
                if (parts.isEmpty()) "Connected · idle" else "Streaming · " + parts.joinToString("+")
            }
        }
    }

    // ------------------------------------------------------------------- mDNS

    private fun registerMdns() {
        try {
            val m = getSystemService(Context.NSD_SERVICE) as NsdManager
            nsdManager = m
            val info = NsdServiceInfo().apply {
                serviceName = "BTBridge " + deviceName()
                serviceType = Proto.SERVICE_TYPE
                port = Proto.PORT
                setAttribute(Proto.MDNS_TXT_NAME, deviceName())
                setAttribute(Proto.MDNS_TXT_PROTO, "1")
            }
            val listener = object : NsdManager.RegistrationListener {
                override fun onServiceRegistered(i: NsdServiceInfo) {
                    Log.i(TAG, "mDNS registered: ${i.serviceName}")
                }

                override fun onRegistrationFailed(i: NsdServiceInfo, errorCode: Int) {
                    Log.w(TAG, "mDNS registration failed: $errorCode")
                }

                override fun onServiceUnregistered(i: NsdServiceInfo) {
                    Log.i(TAG, "mDNS unregistered")
                }

                override fun onUnregistrationFailed(i: NsdServiceInfo, errorCode: Int) {
                    Log.w(TAG, "mDNS unregistration failed: $errorCode")
                }
            }
            registrationListener = listener
            m.registerService(info, NsdManager.PROTOCOL_DNS_SD, listener)
        } catch (t: Throwable) {
            Log.w(TAG, "mDNS unavailable: $t")
        }
    }

    private fun unregisterMdns() {
        val m = nsdManager
        val l = registrationListener
        registrationListener = null
        nsdManager = null
        if (m != null && l != null) {
            try {
                m.unregisterService(l)
            } catch (t: Throwable) {
                Log.w(TAG, "mDNS unregister: $t")
            }
        }
    }

    // ---------------------------------------------------------------- network

    private fun networkKind(): String {
        val ip = localIpv4() ?: return "no network"
        return when {
            ip.startsWith("192.168.42.") -> "USB tethering"
            ip.startsWith("192.168.") || ip.startsWith("10.") || is172Private(ip) -> "Wi-Fi"
            else -> "network"
        }
    }

    private fun is172Private(ip: String): Boolean {
        val p = ip.split(".")
        return p.size == 4 && p[0] == "172" && (p[1].toIntOrNull() ?: 0) in 16..31
    }

    private fun localIpv4(): String? {
        try {
            val ifaces = NetworkInterface.getNetworkInterfaces() ?: return null
            var fallback: String? = null
            for (ni in ifaces) {
                if (!ni.isUp || ni.isLoopback) continue
                for (addr in ni.inetAddresses) {
                    if (addr is Inet4Address && !addr.isLoopbackAddress) {
                        val ip = addr.hostAddress ?: continue
                        val name = ni.name.lowercase()
                        if (name.startsWith("wlan") || name.startsWith("ap") || name.startsWith("swlan")) {
                            return ip
                        }
                        if (name.startsWith("rndis") || name.startsWith("usb")) return ip
                        if (fallback == null) fallback = ip
                    }
                }
            }
            return fallback
        } catch (t: Throwable) {
            return null
        }
    }
}

// ---------------------------------------------------------------------------
// One connected PC
// ---------------------------------------------------------------------------

class ClientSession(
    private val ctx: Context,
    private val svc: BridgeService,
    private val socket: Socket
) {
    private val tag = "BTBridge/client"
    private val writer = FrameWriter(socket.getOutputStream())
    private val reader = FrameReader(socket.getInputStream())

    private var readerThread: Thread? = null
    private var statThread: Thread? = null

    @Volatile
    private var closed = false

    private var audio: AudioCapture? = null
    private var camera: CameraCapture? = null
    private var playout: AudioPlayout? = null

    private val cfgLock = Any()
    @Volatile
    var mic = false
        private set
    @Volatile
    var cam = false
        private set
    @Volatile
    private var camW = 640
    @Volatile
    private var camH = 480
    @Volatile
    private var fps = 10
    @Volatile
    private var facing = "back"
    @Volatile
    private var audioOut = false

    private val camRate = RateMeter()

    val micActive: Boolean get() = audio != null
    val camActive: Boolean get() = camera != null

    fun start() {
        socket.tcpNoDelay = true
        socket.keepAlive = true
        sendJson(JSONObject().apply {
            put("t", "hello")
            put("proto", 1)
            put("name", BridgeService.deviceName())
            put("mic", ctx.checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED)
            put("cam", ctx.checkSelfPermission(Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED)
            put("audioOut", true)
        })

        svc.onClientReady(this)

        readerThread = Thread({ readLoop() }, "btbridge-read").also {
            it.isDaemon = true
            it.start()
        }
        statThread = Thread({ statLoop() }, "btbridge-stat").also {
            it.isDaemon = true
            it.start()
        }
    }

    private fun readLoop() {
        try {
            while (!closed) {
                val f = reader.next() ?: break
                when (f.type) {
                    Proto.TYPE_JSON -> handleJson(f.payload)
                    Proto.TYPE_PCM_STEREO -> playout?.write(f.payload)
                    else -> Log.w(tag, "ignoring inbound frame type ${f.type}")
                }
            }
        } catch (t: Throwable) {
            if (!closed) Log.i(tag, "reader ended: $t")
        } finally {
            close()
        }
    }

    private fun handleJson(payload: ByteArray) {
        val obj = try {
            JSONObject(String(payload, StandardCharsets.UTF_8))
        } catch (t: Throwable) {
            sendErr("bad json")
            return
        }
        when (obj.optString("t")) {
            "cfg" -> applyCfg(obj)
            "ping" -> sendJson(JSONObject().put("t", "pong"))
            "stop" -> {
                stopCapture()
                svc.onCaptureStateChanged()
                sendStat()
            }
            else -> Log.w(tag, "unknown control message: ${obj.optString("t")}")
        }
    }

    private fun applyCfg(obj: JSONObject) {
        var geometryChanged = false
        var outChanged = false
        synchronized(cfgLock) {
            if (obj.has("mic")) mic = obj.optBoolean("mic", mic)
            if (obj.has("cam")) cam = obj.optBoolean("cam", cam)
            if (obj.has("camW")) {
                val w = obj.optInt("camW", camW)
                if (w != camW) geometryChanged = true
                camW = w
            }
            if (obj.has("camH")) {
                val h = obj.optInt("camH", camH)
                if (h != camH) geometryChanged = true
                camH = h
            }
            if (obj.has("fps")) {
                val f = obj.optInt("fps", fps).coerceIn(1, 60)
                if (f != fps) geometryChanged = true
                fps = f
            }
            if (obj.has("facing")) {
                val f = if (obj.optString("facing", facing).equals("front", true)) "front" else "back"
                if (f != facing) {
                    facing = f
                    geometryChanged = true
                }
            }
            if (obj.has("audioOut")) {
                val ao = obj.optBoolean("audioOut", audioOut)
                if (ao != audioOut) {
                    audioOut = ao
                    outChanged = true
                }
            }
        }
        val micWanted = mic
        val camWanted = cam
        if (micWanted && audio == null) startAudio()
        if (!micWanted && audio != null) {
            audio?.stop()
            audio = null
        }
        if (outChanged) {
            if (audioOut) startPlayout() else stopPlayout()
        }
        if (camWanted) {
            if (camera == null || geometryChanged) {
                camera?.stop()
                camera = null
                startCamera()
            }
        } else if (camera != null) {
            camera?.stop()
            camera = null
        }
        svc.onCaptureStateChanged()
        sendStat()
    }

    private fun startAudio() {
        val a = AudioCapture(this)
        audio = a
        a.start()
    }

    private fun startCamera() {
        val c = CameraCapture(ctx, this, camW, camH, fps, facing)
        camera = c
        c.start()
    }

    private fun startPlayout() {
        stopPlayout()
        val p = try {
            AudioPlayout()
        } catch (t: Throwable) {
            sendErr("audio out: ${t.message}")
            return
        }
        playout = p
    }

    private fun stopPlayout() {
        val p = playout
        playout = null
        try {
            p?.stop()
        } catch (_: Throwable) {
        }
    }

    private fun stopCapture() {
        synchronized(cfgLock) {
            mic = false
            cam = false
            audioOut = false
        }
        audio?.stop()
        audio = null
        camera?.stop()
        camera = null
        stopPlayout()
        camRate.reset()
    }

    private fun statLoop() {
        while (!closed) {
            try {
                Thread.sleep(2000)
            } catch (t: Throwable) {
                break
            }
            if (closed) break
            // Keep the advertised state honest if a capture thread died on its own.
            if (audio != null && !audio!!.isRunning) {
                audio?.stop()
                audio = null
            }
            if (camera != null && !camera!!.isRunning) {
                camera?.stop()
                camera = null
            }
            sendStat()
        }
    }

    fun sendStat() {
        val c = camera
        val json = JSONObject().apply {
            put("t", "stat")
            put("mic", audio != null)
            put("cam", c != null)
            put("fps", ((c?.rate() ?: 0.0) * 10).roundToInt() / 10.0)
            put("camW", c?.width ?: camW)
            put("camH", c?.height ?: camH)
            put("facing", facing)
            put("audioOut", playout != null)
        }
        sendJson(json)
    }

    fun sendJson(obj: JSONObject) {
        if (closed) return
        try {
            writer.send(Proto.TYPE_JSON, obj.toString().toByteArray(StandardCharsets.UTF_8))
        } catch (t: Throwable) {
            close()
        }
    }

    fun sendErr(msg: String) {
        sendJson(JSONObject().apply {
            put("t", "err")
            put("msg", msg)
        })
    }

    /** Blocking audio write: mic frames must not be dropped. */
    fun sendAudio(buf: ByteArray) {
        if (closed) return
        try {
            writer.send(Proto.TYPE_PCM_MONO, buf)
        } catch (t: Throwable) {
            close()
        }
    }

    /** Non-blocking JPEG write: frames are dropped when the socket is slow. */
    fun sendJpeg(bytes: ByteArray): Boolean {
        if (closed || writer.failed) return false
        return writer.trySendOrDrop(Proto.TYPE_JPEG, bytes)
    }

    fun onCameraFpsTick() {
        camRate.tick()
    }

    fun close() {
        if (closed) return
        closed = true
        stopCapture()
        try {
            writer.close()
        } catch (_: Throwable) {
        }
        try {
            socket.close()
        } catch (_: Throwable) {
        }
        readerThread?.interrupt()
        statThread?.interrupt()
        svc.onClientGone(this)
    }
}

// ---------------------------------------------------------------------------
// Microphone: AudioRecord -> PCM16 mono 48 kHz, 20 ms (1920 byte) frames
// ---------------------------------------------------------------------------

class AudioCapture(private val session: ClientSession) {
    private var record: AudioRecord? = null
    private var thread: Thread? = null

    @Volatile
    var isRunning = false
        private set

    fun start() {
        val minBuf = try {
            AudioRecord.getMinBufferSize(
                Proto.SAMPLE_RATE,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT
            )
        } catch (t: Throwable) {
            -1
        }
        if (minBuf <= 0) {
            session.sendErr("microphone unavailable")
            return
        }
        val bufBytes = max(minBuf, Proto.FRAME_BYTES * 4)
        val rec = try {
            // No AGC / noise suppression: leave the default processing untouched.
            AudioRecord(
                MediaRecorder.AudioSource.MIC,
                Proto.SAMPLE_RATE,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT,
                bufBytes
            )
        } catch (t: Throwable) {
            session.sendErr("microphone unavailable: ${t.message}")
            return
        }
        if (rec.state != AudioRecord.STATE_INITIALIZED) {
            session.sendErr("microphone unavailable")
            try {
                rec.release()
            } catch (_: Throwable) {
            }
            return
        }
        record = rec
        try {
            rec.startRecording()
        } catch (t: Throwable) {
            session.sendErr("microphone start failed: ${t.message}")
            try {
                rec.release()
            } catch (_: Throwable) {
            }
            record = null
            return
        }
        isRunning = true
        thread = Thread({ loop(rec) }, "btbridge-mic").also {
            it.isDaemon = true
            it.start()
        }
    }

    private fun loop(rec: AudioRecord) {
        val buf = ByteArray(Proto.FRAME_BYTES)
        var filled = 0
        try {
            while (isRunning) {
                val n = rec.read(buf, filled, buf.size - filled)
                if (n < 0) {
                    session.sendErr("microphone read error $n")
                    break
                }
                if (n == 0) continue
                filled += n
                if (filled == buf.size) {
                    session.sendAudio(buf)
                    filled = 0
                }
            }
        } catch (t: Throwable) {
            if (isRunning) session.sendErr("microphone stopped: ${t.message}")
        } finally {
            isRunning = false
        }
    }

    fun stop() {
        isRunning = false
        val rec = record
        record = null
        try {
            rec?.stop()
        } catch (_: Throwable) {
        }
        try {
            rec?.release()
        } catch (_: Throwable) {
        }
        thread?.interrupt()
        thread = null
    }
}

// ---------------------------------------------------------------------------
// Camera: Camera2 + ImageReader(JPEG), drops frames when the socket is slow
// ---------------------------------------------------------------------------

class CameraCapture(
    private val ctx: Context,
    private val session: ClientSession,
    private val reqW: Int,
    private val reqH: Int,
    private val reqFps: Int,
    private val reqFacing: String
) {
    private val tag = "BTBridge/cam"
    private val sending = AtomicBoolean(false)
    private val mainHandler = Handler(android.os.Looper.getMainLooper())

    private var thread: HandlerThread? = null
    private var handler: Handler? = null
    private var manager: CameraManager? = null
    private var device: CameraDevice? = null
    private var captureSession: CameraCaptureSession? = null
    private var imageReader: ImageReader? = null

    @Volatile
    var width = reqW
        private set

    @Volatile
    var height = reqH
        private set

    @Volatile
    var isRunning = false
        private set

    private val captureRunnable = object : Runnable {
        override fun run() {
            if (!isRunning) return
            val s = captureSession
            val d = device
            val r = imageReader
            if (s == null || d == null || r == null) return
            try {
                val b = d.createCaptureRequest(CameraDevice.TEMPLATE_RECORD)
                b.addTarget(r.surface)
                b.set(CaptureRequest.CONTROL_MODE, CameraMetadata.CONTROL_MODE_AUTO)
                s.capture(b.build(), null, handler)
            } catch (t: Throwable) {
                Log.w(tag, "capture failed: $t")
            }
            val interval = max(33L, (1000L / max(1, reqFps)))
            handler?.postDelayed(this, interval)
        }
    }

    private val meter = RateMeter()

    fun rate(): Double = meter.rate()

    fun start() {
        if (isRunning) return
        isRunning = true
        val ht = HandlerThread("btbridge-cam").also { it.start() }
        thread = ht
        val h = Handler(ht.looper)
        handler = h
        h.post { open() }
    }

    private fun open() {
        if (!isRunning) return
        try {
            manager = ctx.getSystemService(Context.CAMERA_SERVICE) as CameraManager
            val id = pickCamera(manager!!)
            if (id == null) {
                fail("no camera")
                return
            }
            val chars = manager!!.getCameraCharacteristics(id)
            val map = chars.get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP)
            val sizes = map?.getOutputSizes(ImageFormat.JPEG)
            if (sizes == null || sizes.isEmpty()) {
                fail("camera has no JPEG output")
                return
            }
            val size = chooseSize(sizes, reqW, reqH)
            width = size.width
            height = size.height
            val r = ImageReader.newInstance(size.width, size.height, ImageFormat.JPEG, 2)
            r.setOnImageAvailableListener({ ir -> onImage(ir) }, handler)
            imageReader = r

            manager!!.openCamera(id, object : CameraDevice.StateCallback() {
                override fun onOpened(camera: CameraDevice) {
                    if (!isRunning) {
                        camera.close()
                        return
                    }
                    device = camera
                    createSession(camera)
                }

                override fun onDisconnected(camera: CameraDevice) {
                    camera.close()
                    if (device === camera) device = null
                }

                override fun onError(camera: CameraDevice, error: Int) {
                    camera.close()
                    if (device === camera) device = null
                    fail(if (error == ERROR_CAMERA_IN_USE) "camera in use" else "camera error $error")
                }
            }, handler)
        } catch (t: CameraAccessException) {
            fail("camera in use")
        } catch (t: Throwable) {
            fail("camera error: ${t.message}")
        }
    }

    private fun createSession(camera: CameraDevice) {
        val r = imageReader ?: return
        try {
            @Suppress("DEPRECATION")
            camera.createCaptureSession(
                listOf(r.surface),
                object : CameraCaptureSession.StateCallback() {
                    override fun onConfigured(s: CameraCaptureSession) {
                        if (!isRunning) {
                            s.close()
                            return
                        }
                        captureSession = s
                        handler?.post(captureRunnable)
                        session.sendStat()
                    }

                    override fun onConfigureFailed(s: CameraCaptureSession) {
                        fail("camera configure failed")
                    }
                },
                handler
            )
        } catch (t: Throwable) {
            fail("camera error: ${t.message}")
        }
    }

    private fun onImage(ir: ImageReader) {
        val image = try {
            ir.acquireNextImage()
        } catch (t: Throwable) {
            null
        } ?: return
        try {
            if (!sending.compareAndSet(false, true)) return // previous frame still going out
            val buf = image.planes[0].buffer
            val bytes = ByteArray(buf.remaining())
            buf.get(bytes)
            if (session.sendJpeg(bytes)) {
                meter.tick()
                session.onCameraFpsTick()
            }
        } finally {
            sending.set(false)
            try {
                image.close()
            } catch (_: Throwable) {
            }
        }
    }

    private fun fail(msg: String) {
        session.sendErr(msg)
        isRunning = false
        mainHandler.post { stopInternal(notify = false) }
    }

    private fun stopInternal(notify: Boolean) {
        isRunning = false
        handler?.removeCallbacksAndMessages(null)
        try {
            captureSession?.close()
        } catch (_: Throwable) {
        }
        captureSession = null
        try {
            device?.close()
        } catch (_: Throwable) {
        }
        device = null
        try {
            imageReader?.close()
        } catch (_: Throwable) {
        }
        imageReader = null
        meter.reset()
        if (notify) session.sendStat()
    }

    fun stop() {
        isRunning = false
        handler?.removeCallbacks(captureRunnable)
        val ht = thread
        stopInternal(notify = false)
        thread = null
        handler = null
        try {
            ht?.quitSafely()
        } catch (_: Throwable) {
        }
    }

    private fun pickCamera(m: CameraManager): String? {
        val ids = m.cameraIdList
        if (ids.isEmpty()) return null
        val wantFront = reqFacing.equals("front", ignoreCase = true)
        var back: String? = null
        var front: String? = null
        for (id in ids) {
            try {
                when (m.getCameraCharacteristics(id).get(CameraCharacteristics.LENS_FACING)) {
                    CameraCharacteristics.LENS_FACING_BACK -> if (back == null) back = id
                    CameraCharacteristics.LENS_FACING_FRONT -> if (front == null) front = id
                }
            } catch (_: Throwable) {
            }
        }
        return if (wantFront) (front ?: back ?: ids[0]) else (back ?: front ?: ids[0])
    }

    private fun chooseSize(sizes: Array<Size>, w: Int, h: Int): Size {
        val targetAspect = w.toDouble() / h.toDouble()
        var best = sizes[0]
        var bestScore = Double.MAX_VALUE
        for (s in sizes) {
            if (s.width < 160 || s.height < 120) continue
            val ar = s.width.toDouble() / s.height.toDouble()
            val aspectScore = abs(ar - targetAspect) / targetAspect
            val areaRatio = (s.width.toLong() * s.height.toLong()).toDouble() / (w.toLong() * h.toLong())
            val areaScore = abs(kotlin.math.ln(max(0.01, areaRatio)))
            val score = aspectScore * 4.0 + areaScore
            if (score < bestScore) {
                bestScore = score
                best = s
            }
        }
        return best
    }
}

// ---------------------------------------------------------------------------
// Audio out: play PCM16 stereo 48 kHz frames sent by the PC
// ---------------------------------------------------------------------------

class AudioPlayout {
    private val tag = "BTBridge/out"
    private val track: AudioTrack

    @Volatile
    private var running = true

    init {
        val minBuf = AudioTrack.getMinBufferSize(
            48000,
            AudioFormat.CHANNEL_OUT_STEREO,
            AudioFormat.ENCODING_PCM_16BIT
        )
        val bufBytes = max(minBuf, 1920 * 6)
        track = AudioTrack(
            AudioAttributes.Builder()
                .setUsage(AudioAttributes.USAGE_MEDIA)
                .setContentType(AudioAttributes.CONTENT_TYPE_MUSIC)
                .build(),
            AudioFormat.Builder()
                .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                .setSampleRate(48000)
                .setChannelMask(AudioFormat.CHANNEL_OUT_STEREO)
                .build(),
            bufBytes,
            AudioTrack.MODE_STREAM,
            AudioManager.AUDIO_SESSION_ID_GENERATE
        )
        track.play()
        Log.i(tag, "playout open (${bufBytes} bytes)")
    }

    /** Called from the socket reader: never block the control channel. */
    fun write(pcm: ByteArray) {
        if (!running) return
        try {
            track.write(pcm, 0, pcm.size, AudioTrack.WRITE_NON_BLOCKING)
        } catch (t: Throwable) {
            Log.w(tag, "write failed: $t")
        }
    }

    fun stop() {
        running = false
        try {
            track.pause()
            track.flush()
        } catch (_: Throwable) {
        }
        try {
            track.release()
        } catch (_: Throwable) {
        }
        Log.i(tag, "playout closed")
    }
}
