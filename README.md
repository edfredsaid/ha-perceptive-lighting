# ✨ Perceptive Lighting for Home Assistant

**Perceptive Lighting** is a human-centric illuminance engine and adaptive room lighting controller for Home Assistant, built on architectural daylighting science and visual psychophysics.

Rather than relying on static raw lux thresholds (where `50 lx` feels blindingly bright at midnight, cozy on a rainy winter afternoon, and like a pitch-black cave on a sunny summer midday), **Perceptive Lighting** computes how bright a room *actually feels* (`0–100%`) and how much supplemental light is needed (`Lighting Deficit 0–100%`).

---

## 🧠 Core Architectural & Psychophysical Engine

1. **Weber-Fechner Logarithmic Perception:** Compresses raw sensor readings onto a perceptual logarithmic curve matching human photoreceptors.
2. **Outdoor Visual Adaptation ("The Cave Effect"):** Tracks outdoor illuminance (`sensor.*` or synthetic fallback from `sun.sun` + `weather.*`) to shift indoor target comfort levels dynamically to overcome simultaneous window contrast.
3. **Mean Room Surface Exitance (MRSE) — Diffuse vs. Direct Light:** Uses cloud cover, weather state, and window orientation to distinguish between diffuse overcast sky light (which wraps walls and ceilings evenly) and harsh direct solar beams (which create high-contrast glare patches and dark interior shadows).
4. **Multi-Sensor Internal Contrast Gradient:** When a room has multiple lux sensors (e.g., near a window vs. in an alcove), their real-time divergence measures the internal brightness gradient directly.
5. **Day-1 Historical Recorder Bootstrap:** Automatically queries the past 10 days of Home Assistant `recorder` history alongside solar azimuth/elevation to learn which direction each window and outdoor sensor faces and calibrate per-sensor placement multipliers automatically.

---

## 🎛️ Flexible Room Modes & Built-in Controller

Each room can be configured in one of three operating modes:
* **Sensor Only Mode:** Exposes `Perceptive Illuminance`, `Natural Perceptive Illuminance`, `Lighting Deficit`, and `Perceptive State` (`dark`, `dim`, `comfortable`, `bright`) for custom automations (such as "wasp-in-a-box" rooms).
* **Simple Controller Mode (Hallway / Transit):** Fast, lightweight presence + deficit controller for transit spaces.
* **Full Adaptive Controller Mode (Multi-Purpose Living Spaces):**
  * **Multi-Use Room Activities:** Switch seamlessly between *Relaxing/Chilling*, *Working/Focus*, *Reading*, *Watching Film*, *Wind-Down*, or *Bright Task*.
  * **Calibrated Adaptive Scenes:** Link existing Home Assistant scenes or snapshot your current room lights (`perceptive_lighting.capture_adaptive_scene`) at the current light level. Lights that are off in the scene stay off, while active lights scale their brightness smoothly around that reference calibration as daylight changes.
  * **Anti-Flicker Cooldowns & Gradual Transitions:** Enforces a configurable **10-minute on/off lockout** and **5-minute gradual adjustment interval** with slow transitions so passing clouds never cause lights to yo-yo.
  * **Two-Tier Manual Control:**
    * **Soft Manual Override:** Automatically detects physical switch, remote, or dashboard changes via Home Assistant event context and holds your manual setting until the room is vacated or natural illuminance shifts substantially ($\ge 32\%$).
    * **Hard Room Automation Lock:** A dedicated `switch.<room>_automation_lock` entity and `lock_room` / `unlock_room` actions that disable all presence and lighting automation for that room until unlocked.
  * **Area-Bound Auto-Discovery with Exclusion Blacklist:** Select a Home Assistant Area and all illuminance sensors, presence sensors, lights, scenes, and (optionally) `media_player` entities (to hold occupancy while watching TV/media) are included automatically—exclude specific entities by exception.

---

## 🖼️ Bundled Custom Lovelace Cards

Installing **Perceptive Lighting** automatically registers two custom dashboard cards in the Home Assistant Card Picker (zero separate frontend installation required):

1. **`Perceptive Lighting: Room Card` (`custom:perceptive-room-card`):**
   * Pick a room from a single dropdown in the visual card editor.
   * Displays live Perceptive Illuminance vs. Daylight-Only bar, one-tap Adaptive Scene pills, Automation Lock / Manual Override toggle, **`[ - Too Bright ]`** / **`[ + Needs More Light ]`** micro-step feel tuning, one-tap **Capture Scene** drawer, **Phone Magnetometer Window Compass** calibration, and live Area Entity Blacklist checkboxes.
2. **`Perceptive Lighting: Whole-Home Card` (`custom:perceptive-home-card`):**
   * Whole-home overview of Outdoor Sky Lux, Diffusion Factor, Solar Elevation, and every configured room's live comfort state and lock controls.

---

## 📦 Installation via HACS

1. Open **HACS** in Home Assistant $\rightarrow$ **Integrations** $\rightarrow$ top-right menu (`⋮`) $\rightarrow$ **Custom repositories**.
2. Add your repository URL with category **Integration**.
3. Click **Download** on **Perceptive Lighting** and restart Home Assistant.
4. Go to **Settings $\rightarrow$ Devices & Services $\rightarrow$ Add Integration $\rightarrow$ Perceptive Lighting**.
