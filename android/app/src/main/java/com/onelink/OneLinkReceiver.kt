package com.onelink

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log

/**
 * Automation entry point: adb shell am broadcast -a com.onelink.TOGGLE
 * (also START / STOP). Every path starts the service through the foreground
 * contract, never from an unbounded background context.
 */
class OneLinkReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context?, intent: Intent?) {
        val ctx = context ?: return
        when (intent?.action) {
            OneLinkService.ACTION_TOGGLE -> {
                if (OneLinkService.running) OneLinkService.stop(ctx) else OneLinkService.start(ctx)
            }
            OneLinkService.ACTION_START -> OneLinkService.start(ctx)
            OneLinkService.ACTION_STOP -> OneLinkService.stop(ctx)
            "com.onelink.CLIPSET" -> {
                val text = intent.getStringExtra("text") ?: ""
                if (text.isNotEmpty()) OneLinkService.pushClip(ctx, text)
            }
            else -> Log.w("OneLink/rx", "unknown action: ${intent?.action}")
        }
    }
}
