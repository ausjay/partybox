# PartyBox UI Design Spec (v1)

## Vibe
- Dark neon “bar/kitchen” command center
- Touch-friendly (big tap targets, generous spacing)
- Subtle/faint neon grid texture in the background (NOT loud)

## Layout + UX
- Primary surfaces are “glass panels”: dark, slightly translucent, soft border
- Admin = command center dashboard layout (cards + sections + status pills)
- Minimal scrolling where possible; when scrolling is needed, keep it smooth with clear sections
- Status always visible (top-right pills): System Online, Mode (PartyBox/Spotify), Requests (Open/Locked)
- Big controls for mounted touchscreen:
  - Resume/Pause, Skip, Stop (pause+clear)
  - Mute/Unmute
  - Mode switch PartyBox / Spotify
  - Lock Requests / Clear Queue

## Design Tokens (CSS Vars)
Base / Surfaces:
- --bg-main: #070B1A
- --bg-panel: #0E1426
- --bg-elevated: #131B34
- --bg-border: #1C2746

Brand (from PartyBox neon logo):
- --cyan: #00D4FF
- --cyan-soft: #0099FF
- --magenta: #FF2DAF
- --purple: #C200FF
- --brand-gradient: linear-gradient(90deg, #00D4FF 0%, #FF2DAF 100%)

Status:
- --success: #00FF88
- --warning: #FFB020
- --danger: #FF3B3B

Text:
- --text-primary: #EAF6FF
- --text-secondary: #A8B4CC
- --text-muted: #7C8CA3

## Typography
- Font: system-ui stack
- Headings: bold, high contrast
- Secondary text: muted with --text-secondary / --text-muted

## Spacing / Shape
- Border radius: 14–18px on cards/buttons
- Buttons: min-height 44px (touch), ideally 48–56px on mounted admin
- Panel padding: 16–20px
- Gap between controls: 10–12px

## Effects
- Borders: 1px using --bg-border with low alpha
- Glow accents: only on primary actions (cyan/magenta)
- Background texture: faint neon grid (very low opacity), optional subtle vignette

