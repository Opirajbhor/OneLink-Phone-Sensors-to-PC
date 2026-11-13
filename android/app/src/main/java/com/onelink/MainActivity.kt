package com.onelink

import android.Manifest
import android.animation.Animator
import android.animation.AnimatorSet
import android.animation.ObjectAnimator
import android.animation.ValueAnimator
import android.app.Activity
import android.app.AlertDialog
import android.content.BroadcastReceiver
import android.content.ClipData
import android.content.ClipboardManager
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
import android.text.TextUtils
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast

/**
 * Phone screen, built from the Material 3 design sheet: a compact header with
 * the phone's own name, a hero card with one circular power button, then
 * Connection / Clipboard / Permissions cards.
 *
 * Everything is code — no AndroidX, no XML layouts. Cards are GradientDrawables,
 * icons are framework vector drawables so they tint with the theme, and every
 * colour is a token from res/values(-night)/colors.xml.
 */
class MainActivity : Activity() {

    private lateinit var pillDot: View
    private lateinit var pillText: TextView
    private lateinit var nameHero: TextView
    private lateinit var halo: LiveHalo
    private lateinit var powerMeta: TextView
    private lateinit var disconnectBtn: TextView
    private lateinit var lanDot: View
    private lateinit var lanText: TextView
    private lateinit var addressValue: TextView
    private lateinit var micTile: View
    private lateinit var camTile: View
    private lateinit var micPill: TextView
    private lateinit var camPill: TextView
    private lateinit var permMeta: TextView

    private var pendingStart = false
    private var resumed = false
    private val animators = ArrayList<Animator>()

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
        applySystemBars()
        setContentView(build())
    }

    /** Status and navigation bars take the page colour, with matching icons. */
    private fun applySystemBars() {
        val dark = (resources.configuration.uiMode and
            android.content.res.Configuration.UI_MODE_NIGHT_MASK) ==
            android.content.res.Configuration.UI_MODE_NIGHT_YES
        window.statusBarColor = color(R.color.surface)
        window.navigationBarColor = color(R.color.surface)
        var flags = window.decorView.systemUiVisibility
        flags = if (dark) {
            flags and View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR.inv() and
                View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR.inv()
        } else {
            flags or View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR or
                View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR
        }
        window.decorView.systemUiVisibility = flags
    }

    // ------------------------------------------------------------------ view
    private fun build(): View {
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(color(R.color.surface))
            fitsSystemWindows = false
        }
        root.addView(header())

        val col = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(16), dp(12), dp(16), dp(24))
        }
        col.addView(heroCard())
        col.addView(connectionCard())
        col.addView(permissionsCard())

        val scroll = ScrollView(this).apply {
            isFillViewport = true
            setBackgroundColor(color(R.color.surface))
            addView(col)
        }
        root.addView(scroll, LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f
        ))

        // targetSdk 35 draws edge to edge: keep the header below the status bar
        // and the last card above the navigation bar.
        root.setOnApplyWindowInsetsListener { view, insets ->
            val top: Int
            val bottom: Int
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                val bars = insets.getInsets(
                    android.view.WindowInsets.Type.statusBars() or
                        android.view.WindowInsets.Type.navigationBars()
                )
                top = bars.top
                bottom = bars.bottom
            } else {
                @Suppress("DEPRECATION")
                top = insets.systemWindowInsetTop
                @Suppress("DEPRECATION")
                bottom = insets.systemWindowInsetBottom
            }
            view.setPadding(0, top, 0, bottom)
            col.setPadding(dp(16), dp(12), dp(16), dp(24))
            insets
        }
        return root
    }

    private fun header(): View {
        val bar = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(dp(16), dp(12), dp(16), dp(12))
            setBackgroundColor(color(R.color.surface))
        }
        bar.addView(ImageView(this).apply {
            setImageResource(R.mipmap.ic_launcher)
            contentDescription = null
            layoutParams = LinearLayout.LayoutParams(dp(30), dp(30))
        })

        val titles = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(10), 0, dp(8), 0)
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        }
        val line1 = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        line1.addView(tv("OneLink", 18f, R.color.on_surface, medium = true).apply {
            letterSpacing = -0.01f
        })
        line1.addView(tv("Main Utility", 11f, R.color.on_variant).apply {
            setPadding(dp(6), 0, 0, 0)
        })
        titles.addView(line1)

        val line2 = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            background = rounded(color(R.color.surface_container), dp(999).toFloat())
            setPadding(dp(9), dp(5), dp(10), dp(6))
            minimumHeight = dp(32)
            isClickable = true
            isFocusable = true
            contentDescription = "Rename this phone"
            setOnClickListener { renameDialog() }
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT
            ).also { it.topMargin = dp(4) }
        }
        val name = tv(DevicePrefs.name(this), 12f, R.color.on_surface, medium = true).apply {
            maxLines = 1
            ellipsize = TextUtils.TruncateAt.END
            maxWidth = dp(118)
        }
        line2.addView(name)
        line2.addView(tv("Rename", 11f, R.color.primary, medium = true).apply {
            setPadding(dp(8), 0, 0, 0)
        })
        line2.addView(icon(R.drawable.ic_edit, 14, R.color.primary).apply {
            setPadding(dp(4), 0, 0, 0)
        })
        titles.addView(line2)
        bar.addView(titles)

        bar.addView(tv("v" + versionName(), 11f, R.color.on_variant, medium = true).apply {
            background = rounded(color(R.color.surface_high), dp(999).toFloat())
            setPadding(dp(8), dp(2), dp(8), dp(3))
        })
        bar.addView(avatar(dp(32)).apply {
            layoutParams = LinearLayout.LayoutParams(dp(32), dp(32)).also { it.marginStart = dp(8) }
        })

        return LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            addView(bar)
            addView(View(this@MainActivity).apply {
                setBackgroundColor(color(R.color.surface_high))
                layoutParams = LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT, dp(1)
                )
            })
        }
    }

    private fun heroCard(): View {
        val card = card(dp(20))
        val inner = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_HORIZONTAL
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT
            )
        }

        pillDot = View(this).apply {
            background = circle(color(R.color.outline))
            layoutParams = LinearLayout.LayoutParams(dp(6), dp(6)).also {
                it.marginEnd = dp(6)
                it.gravity = Gravity.CENTER_VERTICAL
            }
        }
        pillText = tv("Starting…", 11f, R.color.on_variant, medium = true)
        inner.addView(LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            background = rounded(color(R.color.surface_container), dp(999).toFloat())
            setPadding(dp(10), dp(5), dp(12), dp(5))
            addView(pillDot)
            addView(pillText)
        })

        // The phone's own name, breathing over a slow halo: the app starts on
        // open, so this is the "it is live" signal instead of a power button.
        val stage = FrameLayout(this).apply {
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, dp(176)
            ).also { it.topMargin = dp(4) }
        }
        halo = LiveHalo(this).apply {
            setAccent(color(R.color.primary))
            layoutParams = FrameLayout.LayoutParams(dp(176), dp(176), Gravity.CENTER)
        }
        stage.addView(halo)
        nameHero = tv(DevicePrefs.name(this), 27f, R.color.on_surface, medium = true).apply {
            gravity = Gravity.CENTER
            letterSpacing = -0.02f
            maxLines = 2
            ellipsize = TextUtils.TruncateAt.END
            setPadding(dp(18), 0, dp(18), 0)
            layoutParams = FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.CENTER
            )
        }
        stage.addView(nameHero)
        inner.addView(stage)

        powerMeta = tv("Live · listening on ${currentAddress()}", 12.5f, R.color.on_variant)
            .apply { gravity = Gravity.CENTER }
        inner.addView(powerMeta)

        disconnectBtn = tv("Disconnect", 12.5f, R.color.on_surface, medium = true).apply {
            gravity = Gravity.CENTER
            background = rounded(color(R.color.surface_container), dp(10).toFloat())
            setPadding(dp(20), dp(10), dp(20), dp(10))
            isClickable = true
            isFocusable = true
            visibility = View.GONE
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT
            ).also { it.topMargin = dp(14) }
            setOnClickListener {
                OneLinkService.dropClient()
                render()
            }
        }
        inner.addView(disconnectBtn)

        card.addView(inner)
        return card
    }

    // ---------------------------------------------------------- animation
    private fun startPulse() {
        if (animators.isNotEmpty()) return
        halo.start()
        nameHero.translationY = 0f

        val float1 = ObjectAnimator.ofFloat(nameHero, "translationY", 0f, -dp(3).toFloat()).apply {
            duration = 2200
            repeatCount = ValueAnimator.INFINITE
            repeatMode = ValueAnimator.REVERSE
            interpolator = android.view.animation.AccelerateDecelerateInterpolator()
        }
        float1.start()
        animators.add(float1)
    }

    private fun stopPulse() {
        halo.stop()
        for (a in animators) {
            try {
                a.cancel()
            } catch (_: Throwable) {
            }
        }
        animators.clear()
        nameHero.translationY = 0f
    }

    private fun connectionCard(): View {
        val card = card(dp(14))

        lanDot = dot(R.color.outline)
        lanText = tv("offline", 11f, R.color.on_variant, medium = true)
        card.addView(headingRow("Connection", LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            addView(lanDot)
            addView(lanText)
        }))

        val addrRow = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        addressValue = tv(currentAddress(), 11.5f, R.color.on_variant).apply {
            typeface = Typeface.MONOSPACE
            setTextIsSelectable(true)
        }
        addrRow.addView(addressValue)
        addrRow.addView(icon(R.drawable.ic_copy, 15, R.color.on_variant).apply {
            setPadding(dp(6), dp(2), dp(2), dp(2))
            setOnClickListener { copyAddress() }
        })
        card.addView(iconRow(R.drawable.ic_router, "Address", addrRow))
        return card
    }

    private fun permissionsCard(): View {
        val card = card(dp(14))
        permMeta = tv("0 of 2 active", 11f, R.color.on_variant, medium = true)
        card.addView(headingRow("Permissions", permMeta))

        val mic = permRow(R.drawable.ic_mic, "Microphone", "Stream audio to PC virtual mic")
        micTile = mic.first
        micPill = mic.second
        card.addView(mic.first.parent as View)
        card.addView(divider())

        val cam = permRow(R.drawable.ic_videocam, "Camera", "Stream preview to PC")
        camTile = cam.first
        camPill = cam.second
        card.addView(cam.first.parent as View)
        return card
    }

    /** Returns the icon tile and the status pill for one permission. */
    private fun permRow(iconRes: Int, label: String, sub: String): Pair<View, TextView> {
        val tile = tile(iconRes, R.color.surface_high, R.color.on_variant)
        val texts = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(10), 0, dp(8), 0)
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        }
        texts.addView(tv(label, 13f, R.color.on_surface, medium = true))
        texts.addView(tv(sub, 11f, R.color.on_variant).apply {
            maxLines = 1
            ellipsize = TextUtils.TruncateAt.END
        })
        val pill = tv("", 11f, R.color.on_secondary_fixed, medium = true).apply {
            background = rounded(color(R.color.secondary_fixed), dp(999).toFloat())
            setPadding(dp(8), dp(3), dp(9), dp(4))
            setCompoundDrawablePadding(dp(4))
        }
        val row = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            minimumHeight = dp(48)
            setPadding(dp(2), dp(6), dp(2), dp(6))
            addView(tile)
            addView(texts)
            addView(pill)
        }
        return Pair(tile, pill)
    }

    // ---------------------------------------------------------------- pieces
    private fun card(pad: Int) = LinearLayout(this).apply {
        orientation = LinearLayout.VERTICAL
        background = rounded(color(R.color.surface_lowest), dp(12).toFloat())
        elevation = dp(1).toFloat()
        setPadding(pad, pad, pad, pad)
        layoutParams = LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT
        ).also { it.topMargin = dp(12) }
    }

    private fun headingRow(title: String, trailing: View): View {
        return LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(dp(2), 0, dp(2), dp(2))
            addView(tv(title.uppercase(), 11f, R.color.on_variant, medium = true).apply {
                letterSpacing = 0.08f
                layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
            })
            addView(trailing)
        }
    }

    private fun iconRow(iconRes: Int, label: String, trailing: View): View {
        return LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            minimumHeight = dp(44)
            setPadding(dp(2), dp(4), dp(2), dp(4))
            addView(icon(iconRes, 18, R.color.on_variant).apply {
                layoutParams = LinearLayout.LayoutParams(dp(18), dp(18)).also { it.marginEnd = dp(8) }
            })
            addView(tv(label, 13f, R.color.on_surface, medium = true).apply {
                layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
            })
            addView(trailing)
        }
    }

    private fun divider(): View = View(this).apply {
        setBackgroundColor(color(R.color.surface_high))
        layoutParams = LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, dp(1)
        ).also { it.topMargin = dp(1); it.bottomMargin = dp(1) }
    }

    private fun dot(colorRes: Int): View = View(this).apply {
        background = circle(color(colorRes))
        layoutParams = LinearLayout.LayoutParams(dp(6), dp(6)).also {
            it.marginEnd = dp(6)
            it.gravity = Gravity.CENTER_VERTICAL
        }
    }

    private fun tile(iconRes: Int, bgRes: Int, tintRes: Int): View =
        LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER
            background = rounded(color(bgRes), dp(8).toFloat())
            layoutParams = LinearLayout.LayoutParams(dp(32), dp(32))
            addView(icon(iconRes, 18, tintRes))
        }

    private fun icon(res: Int, sizeDp: Int, colorRes: Int): ImageView =
        ImageView(this).apply {
            setImageResource(res)
            setColorFilter(color(colorRes))
            layoutParams = LinearLayout.LayoutParams(dp(sizeDp), dp(sizeDp))
        }

    private fun avatar(size: Int): TextView {
        val name = DevicePrefs.name(this)
        val initial = name.firstOrNull { it.isLetter() }?.uppercase()?.toString()
            ?: name.firstOrNull()?.uppercase()?.toString() ?: "O"
        return tv(initial, 14f, R.color.on_primary, medium = true).apply {
            gravity = Gravity.CENTER
            background = circle(color(R.color.primary))
            layoutParams = LinearLayout.LayoutParams(size, size)
            contentDescription = name
            setOnClickListener { renameDialog() }
        }
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

    private fun versionName(): String = try {
        packageManager.getPackageInfo(packageName, 0).versionName ?: "1.6"
    } catch (_: Throwable) {
        "1.6"
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
        resumed = true
        autoStart()
        render()
    }

    override fun onPause() {
        super.onPause()
        ticker.removeCallbacks(tick)
        resumed = false
        stopPulse()
        try {
            unregisterReceiver(stateReceiver)
        } catch (_: Throwable) {
        }
    }

    // ----------------------------------------------------------------- logic
    /** Opening the app means "on": start the service, ask for permissions if needed. */
    private fun autoStart() {
        if (OneLinkService.running) return
        if (hasCapturePermissions()) {
            OneLinkService.start(this)
        } else {
            pendingStart = true
            requestPermissions()
        }
    }

    private fun copyAddress() {
        try {
            val cm = getSystemService(CLIPBOARD_SERVICE) as? ClipboardManager
            cm?.setPrimaryClip(ClipData.newPlainText("OneLink", OneLinkService.listenAddress))
            Toast.makeText(this, "Address copied", Toast.LENGTH_SHORT).show()
        } catch (_: Throwable) {
        }
    }

    private fun render() {
        val running = OneLinkService.running

        // hero ------------------------------------------------------------
        val pcOn = running && OneLinkService.clientConnected
        pillDot.background = circle(color(if (running) R.color.secondary else R.color.outline))
        pillText.text = when {
            pcOn -> "Connected"
            running -> "Live · waiting for a PC"
            else -> "Starting…"
        }
        pillText.setTextColor(color(if (running) R.color.on_surface else R.color.on_variant))

        if (pcOn) {
            // Connected: no motion, just the fact of it.
            stopPulse()
            halo.visibility = View.GONE
            nameHero.text = "Connected"
            nameHero.setTextSize(TypedValue.COMPLEX_UNIT_SP, 32f)
            powerMeta.text = "to " + (OneLinkService.peerName.ifBlank { "your PC" })
            disconnectBtn.visibility = View.VISIBLE
        } else {
            halo.visibility = View.VISIBLE
            disconnectBtn.visibility = View.GONE
            nameHero.setTextSize(TypedValue.COMPLEX_UNIT_SP, 27f)
            nameHero.text = DevicePrefs.name(this)
            powerMeta.text = when {
                !running && !hasCapturePermissions() -> "Grant microphone and camera to go live"
                !running -> "Starting OneLink…"
                else -> "Live · listening on ${currentAddress()}"
            }
            if (resumed) startPulse()
        }
        halo.setAccent(
            color(
                when {
                    !running -> R.color.outline
                    pcOn -> R.color.secondary
                    else -> R.color.primary
                }
            )
        )

        // connection ------------------------------------------------------
        lanDot.background = circle(color(if (running) R.color.secondary else R.color.outline))
        lanText.text = if (running) "LAN OK" else "offline"
        lanText.setTextColor(color(if (running) R.color.secondary else R.color.on_variant))

        addressValue.text = currentAddress()

        // permissions -----------------------------------------------------
        val micOk = granted(Manifest.permission.RECORD_AUDIO)
        val camOk = granted(Manifest.permission.CAMERA)
        paintPerm(micTile, micPill, micOk)
        paintPerm(camTile, camPill, camOk)
        permMeta.text = "${(if (micOk) 1 else 0) + (if (camOk) 1 else 0)} of 2 active"
        permMeta.setTextColor(color(if (micOk && camOk) R.color.secondary else R.color.on_variant))
    }

    private fun paintPerm(tile: View, pill: TextView, ok: Boolean) {
        (tile.background as? GradientDrawable)?.setColor(
            color(if (ok) R.color.secondary_fixed else R.color.surface_high)
        )
        val tint = color(if (ok) R.color.on_secondary_fixed else R.color.on_variant)
        (tile as? LinearLayout)?.getChildAt(0)?.let { (it as? ImageView)?.setColorFilter(tint) }

        pill.text = if (ok) "granted" else "grant"
        pill.setTextColor(color(if (ok) R.color.on_secondary_fixed else R.color.on_primary_fixed_variant))
        (pill.background as? GradientDrawable)?.setColor(
            color(if (ok) R.color.secondary_fixed else R.color.primary_fixed)
        )
        if (ok) {
            val check = getDrawable(R.drawable.ic_check)?.mutate()
            check?.setTint(color(R.color.on_secondary_fixed))
            check?.setBounds(0, 0, dp(13), dp(13))
            pill.setCompoundDrawables(check, null, null, null)
            pill.setOnClickListener(null)
            pill.isClickable = false
        } else {
            pill.setCompoundDrawables(null, null, null, null)
            pill.isClickable = true
            pill.setOnClickListener { requestPermissions() }
        }
    }

    private fun currentAddress(): String {
        val addr = OneLinkService.listenAddress
        if (OneLinkService.running && !addr.startsWith("0.0.0.0")) return addr
        val ip = localIpv4()
        return if (ip != null) "$ip:${Proto.PORT}" else "not on a network"
    }

    /** This phone's own LAN address, so the row is useful before the service runs. */
    private fun localIpv4(): String? {
        return try {
            for (nif in java.net.NetworkInterface.getNetworkInterfaces()) {
                if (!nif.isUp || nif.isLoopback) continue
                for (a in nif.inetAddresses) {
                    if (a.isLoopbackAddress || a !is java.net.Inet4Address) continue
                    val host = a.hostAddress ?: continue
                    if (host.startsWith("169.254.")) continue
                    return host
                }
            }
            null
        } catch (t: Throwable) {
            null
        }
    }

    private fun renameDialog() {
        val edit = EditText(this).apply {
            setText(DevicePrefs.name(this@MainActivity))
            setSelection(text.length)
            isSingleLine = true
            setPadding(dp(18), dp(10), dp(18), dp(10))
        }
        AlertDialog.Builder(this)
            .setTitle("Phone name")
            .setMessage("This is the name your PC sees. Up to ${DevicePrefs.MAX} characters.")
            .setView(edit)
            .setPositiveButton("Save") { _, _ ->
                DevicePrefs.rename(this, edit.text.toString())
                OneLinkService.renamed()
                setContentView(build())
            }
            .setNegativeButton("Cancel", null)
            .show()
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
