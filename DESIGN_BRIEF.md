# OneLink — design brief for Stitch

## 1. What the product is

OneLink turns an Android phone into a PC accessory over one TCP link (Wi-Fi, USB cable with
adb, or USB tethering). Four things travel over that link:

| Feature | Direction | What the user sees |
|---|---|---|
| Microphone | phone → PC | a virtual mic on the PC ("CABLE Output / OneLink Microphone") that Discord, Zoom, OBS pick up |
| Camera | phone → PC | a live preview window on the PC |
| Speaker | PC → phone | PC sound played on the phone, for Bluetooth or wired headphones |
| Clipboard | both ways | two-way text sync with history on both sides |

Two apps, one product: **OneLink for Windows** (the host) and **OneLink for Android** (the phone).
The phone is always the server; the PC dials in. The phone's name is user-editable and is what the
PC shows.

Design reference: **LocalSend** — quiet, dense, no decoration, one accent, real device cards,
settings as a page not a drawer. Not a dashboard, not a gaming app.

## 2. Design tokens

```
Accent        #2F6DF6   (hover #1F57D6, soft fill #E7EFFF)
Success       #17B26A
Danger        #EF4444
Light:  bg #F4F6FA · card #FFFFFF · card-2 #F7F9FC · line #E4E8F0 · text #171A21
        muted #6B7484 · faint #8B95A6
Dark:   bg #0E1117 · card #161A22 · card-2 #1C222C · line #262D39 · text #EDF1F7
        muted #9AA4B4 · faint #6C7686
Radius  12 px cards · 10 px inputs/buttons · 999 px pills
Spacing 4 / 8 / 12 / 16 — cards 14–16 px padding, 12 px gap
Type    UI font (Segoe UI Variable on Windows, Roboto on Android); card title 11 px
        uppercase +0.8 tracking, muted; row label 13 px 600; body 12.5 px; caption 11 px
Controls Switch 34×20, thumb 14; buttons 34–38 px tall; icon buttons 30 px
Density Compact. No large hero images, no gradients except the avatar.
```

## 3. Windows app

**Window** 1100×760 default, minimum 760×540, native title bar, resizable. Header: logo mark 34 px
+ wordmark "OneLink" + one-line subtitle, a centred segmented tab control **Connect | Settings**,
right side a status pill and a theme button. No browser chrome, no address bar, ever.

### Screen A — Connect (the default; this page has exactly one card)

```
┌ header: OneLink   [Connect|Settings]        ● Not connected   ☾ ┐
│                                                                │
│            ┌──────────────────────────────────────────┐        │
│            │ NEARBY PHONES                      0  ⟳  │        │
│            │                                          │        │
│            │   ( )  Rifat's phone        [WI-FI]      │        │
│            │        192.168.0.104:47110               │        │
│            │   ( )  Redmi 7              [USB]        │        │
│            │        adb · forwarded                   │        │
│            │                                          │        │
│            │   ── or type an address ──               │        │
│            │   [ 192.168.0.101:47110        ] [Add]   │        │
│            │                                          │        │
│            │   [        Connect        ]  [Disconnect]│        │
│            └──────────────────────────────────────────┘        │
└────────────────────────────────────────────────────────────────┘
```

Rules
- Card max-width 560, horizontally centred, vertically centred in the window.
- Device row: 40 px avatar (initial on a two-tone circle derived from the name), name 13 px 600,
  `host:port` 11.5 px muted, transport chip (WI-FI / USB / MANUAL) right aligned.
- Selected row: 1 px accent border + 3 % accent fill.
- Empty state inside the card: thin radar rings, phone glyph, "Looking for your phone", one line of
  help, and the three transport hints as compact rows (Wi-Fi / USB / Manual).
- When a phone is on USB, a one-line banner sits under the list: "Phone on USB — connect without
  Wi-Fi" with a single **Use USB** action.
- Camera preview replaces the card **only while the camera switch is on**: a 16:10 stage with the
  live frame, a Back/Front segmented control and a rotate button. Nothing else appears here.
- States to design: searching (radar), found (list), connecting (row shows a spinner + "pairing…"),
  connected (row accent-bordered, pill turns green "Connected · Rifat's phone"), error (pill red,
  one-line reason under the card).

### Screen B — Settings (two-column card grid, full width)

Sections, in this order:

1. **STREAMING** (full-width row of four): Microphone, Camera, Speaker, Clipboard sync — each a
   label, a one-line description, and a 34×20 switch on the right. All off by default.
2. **CAMERA**: Resolution (select 1280×720 / 960×540 / 640×480 / 320×240), Frame rate
   (select 10 / 15 / 30 / 45 / 60 fps), Rotation (button cycling 0° → 90° → 180° → 270°).
3. **AUDIO**: "Silence the PC while streaming" + switch; "Virtual microphone" + name + READY chip;
   "Rename it in Windows" + small Open button.
4. **CLIPBOARD HISTORY**: count chip, a `clear` text button, note that the last 50 entries persist.
5. **ABOUT**: version, protocol v1, one line on tray behaviour.

Row pattern: `label + description` left, control right, 1 px top divider, 11 px vertical padding.

### Windows system surfaces
- Tray icon + menu: Microphone / Camera / Speaker / Clipboard sync (checkable) · Quit.
- Closing the window hides to tray — never a dialog asking what to do.

## 4. Android app

Single screen, one scroll column, 16 px side padding, cards on `bg`.

```
┌ (avatar) OneLink                     v1.4 ┐
│          Rifat's phone  ✎ (tap to rename)   │
│  ┌───────────────────────────────────────┐  │
│  │        ●  Off · tap to start          │  │
│  │             (  big power  )           │  │
│  │  Listening on 47110 · Wi-Fi           │  │
│  └───────────────────────────────────────┘  │
│  ┌ Connection ──────────────────────────┐   │
│  │ Address      192.168.0.104:47110     │   │
│  │ Discovery    _onelink._tcp.          │   │
│  │ PC           not connected           │   │
│  └──────────────────────────────────────┘   │
│  ┌ Clipboard ───────────────────────────┐   │
│  │ recent: 3 items          [Send mine] │   │
│  └──────────────────────────────────────┘   │
│  ┌ Permissions ─────────────────────────┐   │
│  │ Microphone   granted                 │   │
│  │ Camera       granted                 │   │
│  └──────────────────────────────────────┘   │
└─────────────────────────────────────────────┘
```

- **Avatar**: 50 px circle, initial of the phone name, two-tone gradient from a fixed palette keyed
  by the name. Tapping it (or the name) opens the rename dialog.
- **Rename dialog**: title "Phone name", one text field prefilled, helper "This is the name your PC
  sees. Up to 32 characters.", Cancel / Save.
- **Power hero**: one 96 px circular button, green/accent when on, grey when off; status pill above
  it, listening line below.
- **QS tile**: label "OneLink", app logo icon, subtitle = live state ("Off", "Listening", "Streaming
  · mic+cam").
- **Notification**: small logo icon, "OneLink running", one action "Stop".

## 5. Copy-paste prompts for Stitch

Paste the tokens block from §2 first, then one prompt per screen.

**PC — Connect**
> Desktop utility app window, 1100×760, light theme, compact admin-grade density, LocalSend-style
> calm. Top header: 34 px rounded logo, "OneLink" wordmark with subtitle "Phone mic & camera, on
> your PC", centred segmented tabs Connect | Settings, right side a small status pill "Not
> connected" and a theme icon button. Body: a single card, max-width 560, centred both axes. Card
> header "NEARBY PHONES" uppercase 11 px with a count and a refresh icon. Inside: two device rows
> (40 px initial-avatar circle, device name 13 px semibold, "192.168.0.104:47110" 11 px muted, a
> small transport chip "WI-FI" or "USB" at the right); a divider; a manual address input with an
> Add button; then a primary Connect button and a ghost Disconnect button. Second variant: empty
> state with faint radar rings, phone glyph, "Looking for your phone" and three compact transport
> hints. Third variant: connected — the selected row has an accent border, the pill reads
> "Connected · Rifat's phone" in green. No toggles on this screen. Accent #2F6DF6, cards #FFFFFF,
> page #F4F6FA, 1 px #E4E8F0 borders, 12 px radius, subtle shadow.

**PC — Settings**
> Same app, Settings tab active. Two-column grid of cards filling the width. Top full-width card
> "STREAMING" with four rows — Microphone, Camera, Speaker, Clipboard sync — each with a 13 px
> label, 11.5 px muted description, and a small 34×20 switch on the right, all off. Below, left
> column "CAMERA": Resolution select (640×480), Frame rate select (10 fps), Rotation button (0°).
> Right column "AUDIO": "Silence the PC while streaming" with a switch on, "Virtual microphone" with
> a READY chip, "Rename it in Windows" with a small Open button. Bottom row: "CLIPBOARD HISTORY"
> with a count chip and a clear text button, and "ABOUT" with version and protocol. Rows separated
> by 1 px dividers, 11 px vertical padding, no icons except tiny chevrons.

**Android — Home**
> Android app, single scroll column, 16 px padding, light theme, Material-3-flavoured but custom.
> Header: 50 px circular avatar with a white initial on a blue-to-indigo gradient, "OneLink" 20 px
> semibold, below it "Rifat's phone" 13 px muted with a pencil affordance, version pill far right.
> Hero card: status pill "Off · tap to start", a 96 px circular power button, "Listening on 47110 ·
> Wi-Fi" caption. Then a Connection card with label/value rows, a Clipboard card with "recent: 3
> items" and a "Send mine" text button, and a Permissions card with Microphone / Camera rows and a
> "Grant" link on the missing one. Rounded 16 px cards, 12 px gaps, accent #2F6DF6.

**Android — Rename dialog**
> Material alert dialog over the same screen: title "Phone name", a single-line text field with a
> caret, helper text "This is the name your PC sees. Up to 32 characters.", Cancel and Save buttons,
> Save in accent colour.

**Android — QS tile**
> Quick Settings tile, 1×1, dark system background: rounded squircle with the OneLink logo, label
> "OneLink", subtitle "Streaming · mic+cam", active tint accent blue.

## 6. Hard constraints to keep the design honest

1. **Connect page = one card.** No toggles, no meters, no activity log there.
2. **All four switches live in Settings.** Never duplicate them.
3. Nothing may imply a virtual camera or a Bluetooth radio: the PC has no BT, and the camera is a
   preview, not a driver.
4. Windows device names stay "CABLE Output (VB-Audio Virtual Cable)" unless the user renames them in
   Windows — don't draw an "OneLink Microphone" endpoint that does not exist.
5. Worst-case numbers are real: SBC/AAC audio adds ~200 ms; say so where latency matters.
6. Every value on screen must come from a live query — no placeholder metrics.
