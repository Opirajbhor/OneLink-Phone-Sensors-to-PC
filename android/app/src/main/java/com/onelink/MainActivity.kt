package com.onelink

import android.Manifest
import android.app.Activity
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast

/**
 * One screen, LocalSend-flavoured: header, a hero card with a single big power
 * button, a connection card and a permissions card. Built in code — no
 * AndroidX, no XML layout; rounded cards are GradientDrawables and the colours
 * come from res/values(-night)/colors.xml so dark mode follows the system.
 */
class MainActivity : Activity() {

    private lateinit var pillDot: View
    private lateinit var pillText: TextView
    private lateinit var power: TextView
    private lateinit var subLine: TextView
    private lateinit var valueDevice: TextView
    private lateinit var valueAddress: TextView
    private lateinit var valueClient: TextView
    private lateinit var valueMic: TextView
    private lateinit var valueCam: TextView
    private lateinit var grantLink: TextView

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
        setContentView(build())
        power.setOnClickListener { toggle() }
    }

    // ------------------------------------------------------------------ view
    private fun build(): View {
        val col = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(18), dp(28), dp(18), dp(26))
        }
        val page = ScrollView(this).apply {
            setBackgroundColor(color(R.color.bg))
            isFillViewport = true
            addView(col)
        }
        col.addView(header())

        // hero ------------------------------------------------------------
        val hero = card()
        pillDot = View(this).apply {
            background = circle(color(R.color.good))
            layoutParams = LinearLayout.LayoutParams(dp(8), dp(8)).also {
                it.marginEnd = dp(7)
                it.gravity = Gravity.CENTER_VERTICAL
            }
        }
        pillText = tv("Ready", 11.5f, R.color.muted, medium = true)
        val pill = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            background = rounded(color(R.color.accent_soft), dp(999).toFloat())
            setPadding(dp(11), dp(5), dp(12), dp(5))
            addView(pillDot)
            addView(pillText)
        }
        hero.addView(pill)

        power = tv("Turn on", 16f, R.color.accent_ink, medium = true).apply {
            gravity = Gravity.CENTER
            background = rounded(color(R.color.accent), dp(16).toFloat())
            setPadding(0, dp(15), 0, dp(15))
            isClickable = true
            isFocusable = true
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            ).also { it.topMargin = dp(14) }
        }
        hero.addView(power)

        subLine = tv("Tap to start sharing this phone with your PC", 12.5f, R.color.muted).apply {
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            ).also { it.topMargin = dp(10) }
        }
        hero.addView(subLine)
        col.addView(hero)

        // connection ------------------------------------------------------
        val info = card()
        info.addView(sectionTitle("Connection"))
        valueDevice = addRow(info, "Device", OneLinkService.deviceName())
        valueAddress = addRow(info, "Listening on", OneLinkService.listenAddress)
        valueClient = addRow(info, "PC", "not connected")
        val disc = addRow(info, "Discovery", "_onelink._tcp.")
        disc.setTextColor(color(R.color.faint))
        col.addView(info)
        col.addView(clipboardCard())

        // permissions -----------------------------------------------------
        val perms = card()
        perms.addView(sectionTitle("Permissions"))
        valueMic = addRow(perms, "Microphone", "—")
        valueCam = addRow(perms, "Camera", "—")
        grantLink = tv("Grant missing permissions", 12.5f, R.color.accent, medium = true).apply {
            setPadding(0, dp(10), 0, 0)
            isClickable = true
            setOnClickListener { requestPermissions() }
        }
        perms.addView(grantLink)
        col.addView(perms)

        // footer ----------------------------------------------------------
        col.addView(tv(
            "Open OneLink on your PC — it finds this phone by name. " +
                "Add OneLink to Quick Settings for one-tap control.",
            11.5f, R.color.muted
        ).apply {
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            ).also { it.topMargin = dp(16) }
        })

        return page
    }

    private fun clipboardCard(): View {
        val card = card()
        card.addView(sectionTitle("Clipboard"))
        val list = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(0, dp(7), 0, dp(3))
        }
        val items = ClipHistory.list(this, 3)
        if (items.isEmpty()) {
            list.addView(tv("Nothing yet — copy on either side.", 12.5f, R.color.muted))
        } else {
            for (item in items) {
                val row = LinearLayout(this).apply {
                    orientation = LinearLayout.HORIZONTAL
                    gravity = android.view.Gravity.CENTER_VERTICAL
                }
                val text = item.optString("text").replace(Regex("\\s+"), " ")
                row.addView(tv(text.take(64), 12f, R.color.text).apply {
                    maxLines = 1
                    ellipsize = android.text.TextUtils.TruncateAt.END
                    setPadding(0, dp(4), 0, dp(4))
                }, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
                row.addView(tv(
                    if (item.optString("origin") == "pc") "PC" else "PHONE",
                    9.5f, R.color.accent, medium = true
                ))
                list.addView(row)
            }
        }
        card.addView(list)
        val send = tv("Send my clipboard", 12.5f, R.color.accent, medium = true).apply {
            gravity = android.view.Gravity.CENTER
            setPadding(0, dp(11), 0, dp(11))
            background = rounded((color(R.color.accent) and 0x00FFFFFF) or 0x1F000000, 12f)
            isClickable = true
            setOnClickListener { sendClipboard() }
        }
        card.addView(send, LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT
        ).apply { topMargin = dp(4) })
        return card
    }

    private fun sendClipboard() {
        val text = try {
            val cm = getSystemService(CLIPBOARD_SERVICE) as? android.content.ClipboardManager
            cm?.primaryClip?.takeIf { it.itemCount > 0 }
                ?.getItemAt(0)?.coerceToText(this)?.toString()
        } catch (t: Throwable) {
            null
        }
        if (text.isNullOrBlank()) {
            Toast.makeText(this, "Copy something first", Toast.LENGTH_SHORT).show()
            return
        }
        OneLinkService.pushClip(this, text)
        Toast.makeText(this, "Sent to PC", Toast.LENGTH_SHORT).show()
        setContentView(build())
    }

    private fun header(): View {
        val row = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        row.addView(ImageView(this).apply {
            setImageResource(R.mipmap.ic_launcher)
            background = rounded(color(R.color.card), dp(15).toFloat())
            setPadding(dp(5), dp(5), dp(5), dp(5))
            layoutParams = LinearLayout.LayoutParams(dp(48), dp(48))
        })

        val titles = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(12), 0, dp(8), 0)
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        }
        titles.addView(tv("OneLink", 21f, R.color.text, medium = true))
        titles.addView(tv("Phone mic & camera, on your PC", 12f, R.color.muted))
        row.addView(titles)

        row.addView(tv("v" + versionName(), 10.5f, R.color.accent, medium = true).apply {
            background = rounded(color(R.color.accent_soft), dp(999).toFloat())
            setPadding(dp(9), dp(3), dp(9), dp(4))
        })
        return row
    }

    /** Adds a "Label ............ value" row and returns the value view. */
    private fun addRow(parent: LinearLayout, label: String, value: String): TextView {
        val row = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(0, dp(8), 0, dp(8))
        }
        row.addView(tv(label, 12.5f, R.color.muted).apply {
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        })
        val v = tv(value, 12.5f, R.color.text, medium = true).apply {
            gravity = Gravity.END
            setTextIsSelectable(true)
        }
        row.addView(v)
        parent.addView(row)
        return v
    }

    // -------------------------------------------------------------- helpers
    private fun card() = LinearLayout(this).apply {
        orientation = LinearLayout.VERTICAL
        background = getDrawableRes(R.drawable.bg_card)
        elevation = dp(1).toFloat()
        setPadding(dp(16), dp(16), dp(16), dp(16))
        layoutParams = LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.WRAP_CONTENT
        ).also { it.topMargin = dp(14) }
    }

    private fun sectionTitle(text: String) = tv(text.uppercase(), 10.5f, R.color.muted, medium = true)
        .apply {
            letterSpacing = 0.08f
            setPadding(0, 0, 0, dp(6))
        }

    private fun tv(text: String, sp: Float, colorRes: Int, medium: Boolean = false) =
        TextView(this).apply {
            this.text = text
            setTextSize(TypedValue.COMPLEX_UNIT_SP, sp)
            setTextColor(color(colorRes))
            if (medium) typeface = Typeface.create("sans-serif-medium", Typeface.NORMAL)
        }

    private fun circle(argb: Int) = GradientDrawable().apply {
        shape = GradientDrawable.OVAL
        setColor(argb)
    }

    private fun rounded(argb: Int, radius: Float) = GradientDrawable().apply {
        shape = GradientDrawable.RECTANGLE
        cornerRadius = radius
        setColor(argb)
    }

    private fun color(res: Int): Int = getColor(res)

    private fun getDrawableRes(res: Int) = getDrawable(res)

    private fun versionName(): String = try {
        packageManager.getPackageInfo(packageName, 0).versionName ?: "1.1"
    } catch (_: Throwable) {
        "1.1"
    }

    private fun dp(v: Int): Int = (v * resources.displayMetrics.density).toInt()

    // ------------------------------------------------------------- lifecycle
    override fun onResume() {
        super.onResume()
        val filter = IntentFilter(OneLinkService.ACTION_STATE)
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

    // ----------------------------------------------------------------- logic
    private fun toggle() {
        if (OneLinkService.running) {
            OneLinkService.stop(this)
        } else if (hasCapturePermissions()) {
            OneLinkService.start(this)
        } else {
            pendingStart = true
            requestPermissions()
        }
        render()
    }

    private fun render() {
        val running = OneLinkService.running
        pillDot.background = circle(color(if (running) R.color.good else R.color.faint))
        pillText.text = if (running) "Sharing" else "Off"
        pillText.setTextColor(color(if (running) R.color.good else R.color.muted))

        power.text = if (running) "Turn off" else "Turn on"
        power.background = rounded(
            color(if (running) R.color.card2 else R.color.accent),
            dp(16).toFloat()
        )
        power.setTextColor(color(if (running) R.color.text else R.color.accent_ink))

        subLine.text = if (running) OneLinkService.statusText
        else "Tap to start sharing this phone with your PC"

        valueDevice.text = OneLinkService.deviceName()
        valueAddress.text = OneLinkService.listenAddress
        valueClient.text = when {
            !running -> "not connected"
            OneLinkService.clientConnected -> "connected"
            else -> "waiting…"
        }

        val micOk = granted(Manifest.permission.RECORD_AUDIO)
        val camOk = granted(Manifest.permission.CAMERA)
        valueMic.text = if (micOk) "granted" else "missing"
        valueMic.setTextColor(color(if (micOk) R.color.good else R.color.bad))
        valueCam.text = if (camOk) "granted" else "missing"
        valueCam.setTextColor(color(if (camOk) R.color.good else R.color.bad))
        grantLink.visibility = if (micOk && camOk) View.GONE else View.VISIBLE
    }

    private fun granted(perm: String) =
        checkSelfPermission(perm) == PackageManager.PERMISSION_GRANTED

    private fun hasCapturePermissions() =
        granted(Manifest.permission.RECORD_AUDIO) && granted(Manifest.permission.CAMERA)

    private fun requestPermissions() {
        val perms = ArrayList<String>()
        perms.add(Manifest.permission.RECORD_AUDIO)
        perms.add(Manifest.permission.CAMERA)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            perms.add(Manifest.permission.POST_NOTIFICATIONS)
        }
        requestPermissions(perms.toTypedArray(), REQ_PERMS)
    }

    override fun onRequestPermissionsResult(
        requestCode: Int,
        permissions: Array<out String>,
        grantResults: IntArray
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == REQ_PERMS && pendingStart) {
            pendingStart = false
            OneLinkService.start(this)
        }
        render()
    }

    companion object {
        private const val REQ_PERMS = 4711
    }
}
