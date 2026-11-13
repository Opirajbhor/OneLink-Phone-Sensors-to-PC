package com.onelink

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.graphics.drawable.Icon
import android.os.Handler
import android.os.Looper
import android.service.quicksettings.Tile
import android.service.quicksettings.TileService
import android.util.Log

/**
 * Quick Settings tile. Its state is always read from the running service
 * (never a locally cached guess) whenever the tile is visible or the service
 * broadcasts a state change.
 */
class OneLinkTileService : TileService() {

    private var listening = false

    private val stateReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context?, intent: Intent?) {
            refresh()
        }
    }

    override fun onStartListening() {
        super.onStartListening()
        listening = true
        try {
            val filter = IntentFilter(OneLinkService.ACTION_STATE)
            if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.TIRAMISU) {
                registerReceiver(stateReceiver, filter, RECEIVER_NOT_EXPORTED)
            } else {
                registerReceiver(stateReceiver, filter)
            }
        } catch (t: Throwable) {
            Log.w("OneLink/tile", "receiver: $t")
        }
        refresh()
    }

    override fun onStopListening() {
        listening = false
        try {
            unregisterReceiver(stateReceiver)
        } catch (_: Throwable) {
        }
        super.onStopListening()
    }

    override fun onClick() {
        super.onClick()
        if (isLocked) {
            unlockAndRun { performToggle() }
        } else {
            performToggle()
        }
    }

    private fun performToggle() {
        try {
            if (OneLinkService.running) {
                OneLinkService.stop(this)
            } else {
                OneLinkService.start(this)
            }
        } catch (t: Throwable) {
            Log.w("OneLink/tile", "toggle failed: $t")
        }
        // Re-read the real service state once the start/stop has had a moment
        // to take effect instead of assuming the flip succeeded.
        Handler(Looper.getMainLooper()).postDelayed({ refresh() }, 500)
    }

    override fun onTileAdded() {
        super.onTileAdded()
        refresh()
    }

    private fun refresh() {
        val tile = qsTile ?: return
        val on = OneLinkService.running
        tile.state = if (on) Tile.STATE_ACTIVE else Tile.STATE_INACTIVE
        tile.label = "OneLink"
        tile.icon = Icon.createWithResource(this, android.R.drawable.stat_sys_data_bluetooth)
        if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.Q) {
            tile.subtitle = if (on) OneLinkService.statusText else "Off"
        }
        try {
            tile.updateTile()
        } catch (t: Throwable) {
            Log.w("OneLink/tile", "updateTile: $t")
        }
    }
}
