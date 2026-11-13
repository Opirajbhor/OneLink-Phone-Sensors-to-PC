package com.btbridge

import android.Manifest
import android.app.Activity
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.graphics.Color
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.text.InputType
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.widget.LinearLayout
import android.widget.Switch
import android.widget.TextView

/**
 * One screen: big on/off switch, status, device name, listen address.
 * Built entirely in code (no AndroidX, no XML layout, framework theme only).
 */
class MainActivity : Activity() {

    private lateinit var toggle: Switch
    private lateinit var status: TextView
    private lateinit var device: TextView
    private lateinit var address: TextView
    private lateinit var hint: TextView

    private var updating = false
    private var pendingStart = false

    private val ticker = Handler(Looper.getMainLooper())
    private val tick = object : Runnable {
        override fun run() {
            render()
            ticker.postDelayed(this, 1000)
        }
    }

    private val stateReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context?, intent: Intent?) {
            render()
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(24), dp(32), dp(24), dp(24))
            gravity = Gravity.CENTER_HORIZONTAL
            setBackgroundColor(Color.WHITE)
        }

        val title = TextView(this).apply {
            text = "BTBridge"
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 30f)
            setTextColor(Color.BLACK)
            gravity = Gravity.CENTER
        }

        toggle = Switch(this).apply {
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 26f)
            gravity = Gravity.CENTER
            minHeight = dp(120)
            showText = true
            text = "Off"
            setPadding(dp(8), dp(16), dp(8), dp(16))
        }

        status = TextView(this).apply {
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 18f)
            setTextColor(Color.DKGRAY)
            gravity = Gravity.CENTER
            setPadding(0, dp(24), 0, dp(8))
            text = "Off"
        }

        device = TextView(this).apply {
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 15f)
            setTextColor(Color.parseColor("#444444"))
            gravity = Gravity.CENTER
            text = "Device: " + BridgeService.deviceName()
        }

        address = TextView(this).apply {
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 15f)
            setTextColor(Color.parseColor("#444444"))
            gravity = Gravity.CENTER
            setTextIsSelectable(true)
            setTextIsSelectable(true)
            text = "Listen: 0.0.0.0:" + Proto.PORT
        }

        hint = TextView(this).apply {
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 13f)
            setTextColor(Color.parseColor("#777777"))
            gravity = Gravity.CENTER
            setPadding(0, dp(24), 0, 0)
            text = "Discovery: _btbridge._tcp. · port ${Proto.PORT}\n" +
                "Mic and camera permissions are requested on first start."
        }

        root.addView(title)
        root.addView(toggle, LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            LinearLayout.LayoutParams.WRAP_CONTENT
        ))
        root.addView(status)
        root.addView(device)
        root.addView(address)
        root.addView(hint)

        setContentView(root)

        toggle.setOnCheckedChangeListener { _, checked ->
            if (updating) return@setOnCheckedChangeListener
            if (checked) {
                if (hasCapturePermissions()) {
                    BridgeService.start(this)
                } else {
                    pendingStart = true
                    requestPermissions()
                }
            } else {
                BridgeService.stop(this)
            }
            render()
        }
    }

    override fun onResume() {
        super.onResume()
        val filter = IntentFilter(BridgeService.ACTION_STATE)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            registerReceiver(stateReceiver, filter, RECEIVER_NOT_EXPORTED)
        } else {
            registerReceiver(stateReceiver, filter)
        }
        ticker.post(tick)
        render()
    }

    override fun onPause() {
        super.onPause()
        ticker.removeCallbacks(tick)
        try {
            unregisterReceiver(stateReceiver)
        } catch (_: Throwable) {
        }
    }

    private fun render() {
        val running = BridgeService.running
        updating = true
        toggle.isChecked = running
        toggle.text = if (running) "On" else "Off"
        updating = false
        status.text = BridgeService.statusText
        device.text = "Device: " + BridgeService.deviceName()
        address.text = "Listen: " + BridgeService.listenAddress
        hint.text = if (running && !hasCapturePermissions()) {
            "Mic and camera permissions are missing — the PC will get a {\"t\":\"err\"} for those streams."
        } else {
            "Discovery: _btbridge._tcp. · port ${Proto.PORT}\n" +
                "Mic and camera permissions are requested on first start."
        }
    }

    private fun requestPermissions() {
        val perms = ArrayList<String>()
        perms.add(Manifest.permission.RECORD_AUDIO)
        perms.add(Manifest.permission.CAMERA)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            perms.add(Manifest.permission.POST_NOTIFICATIONS)
        }
        requestPermissions(perms.toTypedArray(), REQ_PERMS)
    }

    private fun hasCapturePermissions(): Boolean {
        val mic = checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED
        val cam = checkSelfPermission(Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED
        return mic && cam
    }

    override fun onRequestPermissionsResult(
        requestCode: Int,
        permissions: Array<out String>,
        grantResults: IntArray
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == REQ_PERMS && pendingStart) {
            pendingStart = false
            BridgeService.start(this)
            render()
        }
    }

    private fun dp(v: Int): Int = (v * resources.displayMetrics.density).toInt()

    companion object {
        private const val REQ_PERMS = 4711
    }
}
