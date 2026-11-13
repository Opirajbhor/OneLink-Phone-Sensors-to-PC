package com.btbridge

import java.io.BufferedOutputStream
import java.io.EOFException
import java.io.IOException
import java.io.InputStream
import java.io.OutputStream
import java.util.concurrent.locks.ReentrantLock

/** BTBridge wire protocol v1 constants. */
object Proto {
    const val PORT = 47110

    const val TYPE_PCM_MONO = 0x01   // PCM16 LE, mono, 48 kHz
    const val TYPE_JPEG = 0x02       // one JPEG image
    const val TYPE_JSON = 0x03       // UTF-8 JSON control message
    const val TYPE_PCM_STEREO = 0x04 // PCM16 LE, stereo, 48 kHz (v0.2, playout)

    const val MAX_FRAME = 16 * 1024 * 1024

    const val SAMPLE_RATE = 48000
    const val FRAME_SAMPLES = 960            // 20 ms
    const val FRAME_BYTES = FRAME_SAMPLES * 2 // 1920

    const val SERVICE_TYPE = "_btbridge._tcp."
    const val MDNS_TXT_NAME = "name"
    const val MDNS_TXT_PROTO = "proto"
}

class Frame(val type: Int, val payload: ByteArray)

/**
 * Writes [type][BE u32 length][payload] frames. One instance per socket.
 * Audio uses the blocking [send]; camera frames use [trySendOrDrop] so a slow
 * socket drops frames instead of queueing them.
 */
class FrameWriter(out: OutputStream) {
    private val lock = ReentrantLock()
    private val bos = BufferedOutputStream(out, 64 * 1024)

    @Volatile
    var failed = false
        private set

    @Throws(IOException::class)
    fun send(type: Int, payload: ByteArray, off: Int = 0, len: Int = payload.size) {
        lock.lock()
        try {
            writeLocked(type, payload, off, len)
        } finally {
            lock.unlock()
        }
    }

    /** Returns false when the socket is busy (frame dropped) or broken. */
    fun trySendOrDrop(type: Int, payload: ByteArray): Boolean {
        if (!lock.tryLock()) return false
        return try {
            writeLocked(type, payload, 0, payload.size)
            true
        } catch (t: Throwable) {
            failed = true
            false
        } finally {
            lock.unlock()
        }
    }

    @Throws(IOException::class)
    private fun writeLocked(type: Int, payload: ByteArray, off: Int, len: Int) {
        if (failed) throw IOException("writer closed")
        bos.write(type and 0xFF)
        bos.write((len ushr 24) and 0xFF)
        bos.write((len ushr 16) and 0xFF)
        bos.write((len ushr 8) and 0xFF)
        bos.write(len and 0xFF)
        bos.write(payload, off, len)
        bos.flush()
    }

    fun close() {
        lock.lock()
        try {
            failed = true
            try {
                bos.flush()
            } catch (_: Throwable) {
            }
        } finally {
            lock.unlock()
        }
        try {
            bos.close()
        } catch (_: Throwable) {
        }
    }
}

class FrameReader(private val ins: InputStream) {

    @Throws(IOException::class)
    fun next(): Frame? {
        val type = ins.read()
        if (type < 0) return null
        val len = readInt()
        if (len < 0 || len > Proto.MAX_FRAME) throw IOException("bad frame length $len")
        val payload = ByteArray(len)
        readFully(payload)
        return Frame(type, payload)
    }

    @Throws(IOException::class)
    private fun readInt(): Int {
        val b = ByteArray(4)
        readFully(b)
        return ((b[0].toInt() and 0xFF) shl 24) or
            ((b[1].toInt() and 0xFF) shl 16) or
            ((b[2].toInt() and 0xFF) shl 8) or
            (b[3].toInt() and 0xFF)
    }

    @Throws(IOException::class)
    private fun readFully(b: ByteArray) {
        var off = 0
        while (off < b.size) {
            val n = ins.read(b, off, b.size - off)
            if (n < 0) throw EOFException("socket closed")
            off += n
        }
    }
}

/** Measures a rolling per-second rate (used for the reported camera fps). */
class RateMeter {
    private var windowStart = System.nanoTime()
    private var count = 0
    @Volatile
    private var fps = 0.0

    fun tick() {
        count++
        val now = System.nanoTime()
        val dt = (now - windowStart) / 1e9
        if (dt >= 1.0) {
            fps = count / dt
            count = 0
            windowStart = now
        }
    }

    fun rate(): Double = fps

    fun reset() {
        count = 0
        fps = 0.0
        windowStart = System.nanoTime()
    }
}
