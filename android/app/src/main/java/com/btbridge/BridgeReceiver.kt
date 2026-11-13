package com.btbridge

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log

/**
 * Automation entry point: adb shell am broadcast -a com.btbridge.TOGGLE
 * (also START / STOP). Every path starts the service through the foreground
 * contract, never from an unbounded background context.
 */
class BridgeReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context?, intent: Intent?) {
        val ctx = context ?: return
        when (intent?.action) {
            BridgeService.ACTION_TOGGLE -> {
                if (BridgeService.running) BridgeService.stop(ctx) else BridgeService.start(ctx)
            }
            BridgeService.ACTION_START -> BridgeService.start(ctx)
            BridgeService.ACTION_STOP -> BridgeService.stop(ctx)
            else -> Log.w("BTBridge/rx", "unknown action: ${intent?.action}")
        }
    }
}
