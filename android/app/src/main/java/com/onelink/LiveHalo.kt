package com.onelink

import android.animation.ValueAnimator
import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RadialGradient
import android.graphics.RectF
import android.graphics.Shader
import android.view.View
import android.view.animation.LinearInterpolator

/**
 * "This phone is live" indicator: a soft core glow, three rings expanding out
 * of it on a 1/3-phase stagger, and a comet arc sweeping the rim.
 *
 * One ValueAnimator drives everything at 3.6 s per cycle; each ring derives its
 * radius and opacity from that single phase, so the motion stays perfectly
 * spaced instead of drifting like three independent animations would.
 */
class LiveHalo(context: Context) : View(context) {

    private val corePaint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val ringPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
    }
    private val cometPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeCap = Paint.Cap.ROUND
    }
    private val headPaint = Paint(Paint.ANTI_ALIAS_FLAG)

    private var accent = 0xFF2F6DF6.toInt()
    private var phase = 0f
    private var animator: ValueAnimator? = null
    private var coreShader: RadialGradient? = null
    private var radius = 0f
    private val arcRect = RectF()

    fun setAccent(color: Int) {
        if (color != accent) {
            accent = color
            coreShader = null
            invalidate()
        }
    }

    fun start() {
        if (animator != null) return
        animator = ValueAnimator.ofFloat(0f, 1f).apply {
            duration = 3600
            repeatCount = ValueAnimator.INFINITE
            interpolator = LinearInterpolator()
            addUpdateListener {
                phase = it.animatedValue as Float
                invalidate()
            }
            start()
        }
    }

    fun stop() {
        animator?.cancel()
        animator = null
    }

    override fun onDetachedFromWindow() {
        stop()
        super.onDetachedFromWindow()
    }

    override fun onSizeChanged(w: Int, h: Int, oldw: Int, oldh: Int) {
        super.onSizeChanged(w, h, oldw, oldh)
        radius = minOf(w, h) / 2f
        coreShader = null
    }

    override fun onDraw(canvas: Canvas) {
        val cx = width / 2f
        val cy = height / 2f
        val r = radius

        // soft core
        if (coreShader == null) {
            coreShader = RadialGradient(
                cx, cy, r * 0.72f,
                intArrayOf(alpha(accent, 0.22f), alpha(accent, 0.07f), Color.TRANSPARENT),
                floatArrayOf(0f, 0.58f, 1f),
                Shader.TileMode.CLAMP
            )
        }
        corePaint.shader = coreShader
        canvas.drawCircle(cx, cy, r * 0.72f, corePaint)

        // three rings, evenly staggered, easing out as they grow
        for (i in 0 until 3) {
            val t = (phase + i / 3f) % 1f
            val ease = t * t
            val rr = r * (0.40f + 0.58f * (1f - (1f - t) * (1f - t)))
            ringPaint.color = alpha(accent, 0.30f * (1f - ease) * (1f - ease))
            ringPaint.strokeWidth = dp(1.5f) * (1f - 0.55f * t)
            canvas.drawCircle(cx, cy, minOf(rr, r - dp(1f)), ringPaint)
        }

        // comet arc on the rim
        val rim = r - dp(2.5f)
        arcRect.set(cx - rim, cy - rim, cx + rim, cy + rim)
        val start = phase * 360f
        cometPaint.strokeWidth = dp(2f)
        cometPaint.color = alpha(accent, 0.55f)
        canvas.drawArc(arcRect, start, 58f, false, cometPaint)
        cometPaint.color = alpha(accent, 0.28f)
        canvas.drawArc(arcRect, start + 58f, 26f, false, cometPaint)

        // comet head
        val headAngle = Math.toRadians((start + 84f).toDouble())
        val hx = cx + rim * Math.cos(headAngle)
        val hy = cy + rim * Math.sin(headAngle)
        headPaint.color = alpha(accent, 0.75f)
        canvas.drawCircle(hx.toFloat(), hy.toFloat(), dp(2.2f), headPaint)
    }

    private fun alpha(color: Int, factor: Float): Int {
        val a = (factor.coerceIn(0f, 1f) * 255).toInt()
        return (a shl 24) or (color and 0x00FFFFFF)
    }

    private fun dp(v: Float): Float = v * resources.displayMetrics.density
}
