"""Perceptive Lighting Custom Lovelace Cards (Room Card & Whole-Home Overview Card).

Auto-registered by the custom_components/perceptive_lighting integration.
Supports:
- Visual GUI Editor (Pick a Room from a single dropdown)
- Live Perceptive Comfort Gauge, Daylight vs Artificial breakdown, & Outdoor Sky Status
- One-tap Adaptive Scene & Activity switching
- Live "Capture Current Light Setup as Adaptive Scene" modal
- Micro-step perceptual tuning: [ - Too Bright ] and [ + Needs More Light ]
- Interactive "Point Phone at Window" Web Magnetometer Compass calibration
- Live Area Entity Exclusion (Blacklist) drawer
"""

const DOMAIN = "perceptive_lighting";

const ACTIVITY_META = {
  relaxing: { label: "Chilling", icon: "🛋️" },
  working: { label: "Working", icon: "💻" },
  reading: { label: "Reading", icon: "📖" },
  film: { label: "Film", icon: "🎬" },
  sleep_prep: { label: "Wind-Down", icon: "🌙" },
  transit: { label: "Transit", icon: "🚶" },
  bright_task: { label: "Bright Task", icon: "🔦" },
};

const STATE_COLORS = {
  bright: "#f59e0b",
  comfortable: "#10b981",
  dim: "#6366f1",
  dark: "#475569",
};

function findRoomSensor(hass, roomKey) {
  if (!hass || !hass.states) return null;
  const states = Object.values(hass.states);
  for (const st of states) {
    if (
      st.entity_id.startsWith("sensor.") &&
      st.attributes &&
      st.attributes.room_id &&
      st.attributes.available_scenes !== undefined
    ) {
      if (
        !roomKey ||
        st.attributes.room_id === roomKey ||
        st.attributes.area_id === roomKey ||
        (st.attributes.room_name &&
          st.attributes.room_name.toLowerCase() === String(roomKey).toLowerCase()) ||
        st.entity_id === roomKey
      ) {
        return st;
      }
    }
  }
  return null;
}

function findAllRoomSensors(hass) {
  if (!hass || !hass.states) return [];
  return Object.values(hass.states).filter(
    (st) =>
      st.entity_id.startsWith("sensor.") &&
      st.attributes &&
      st.attributes.room_id &&
      st.attributes.available_scenes !== undefined
  );
}

function findHubOutdoorSensor(hass) {
  if (!hass || !hass.states) return null;
  return (
    Object.values(hass.states).find(
      (st) =>
        st.entity_id.startsWith("sensor.") &&
        st.attributes &&
        st.attributes.diffuse_sky_factor !== undefined &&
        st.attributes.configured_rooms !== undefined
    ) || null
  );
}

function compassDegreesToCardinal(deg) {
  if (deg === null || deg === undefined || isNaN(deg)) return "Auto";
  const dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];
  const idx = Math.round(((deg % 360) < 0 ? deg + 360 : deg) / 45) % 8;
  return `${Math.round(deg)}° (${dirs[idx]})`;
}

const SHARED_STYLES = `
  :host {
    display: block;
  }
  ha-card {
    padding: 18px;
    border-radius: var(--ha-card-border-radius, 16px);
    background: var(--ha-card-background, var(--card-background-color, #1e1e2e));
    color: var(--primary-text-color);
    box-sizing: border-box;
    font-family: var(--paper-font-body1_-_font-family, inherit);
  }
  .header-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    margin-bottom: 12px;
  }
  .title-block {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }
  .room-title {
    font-size: 1.18rem;
    font-weight: 600;
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .subtitle {
    font-size: 0.82rem;
    color: var(--secondary-text-color);
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
  }
  .badges-row {
    display: flex;
    align-items: center;
    gap: 6px;
    flex-wrap: wrap;
  }
  .pill-badge {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    padding: 4px 10px;
    border-radius: 999px;
    font-size: 0.76rem;
    font-weight: 600;
    cursor: pointer;
    user-select: none;
    transition: all 0.18s ease;
    border: 1px solid rgba(128, 128, 128, 0.25);
    background: rgba(128, 128, 128, 0.12);
  }
  .pill-badge:hover {
    filter: brightness(1.12);
  }
  .pill-badge.locked {
    background: rgba(239, 68, 68, 0.18);
    color: #ef4444;
    border-color: rgba(239, 68, 68, 0.4);
  }
  .pill-badge.override {
    background: rgba(245, 158, 11, 0.2);
    color: #f59e0b;
    border-color: rgba(245, 158, 11, 0.45);
  }
  .pill-badge.auto {
    background: rgba(16, 185, 129, 0.15);
    color: #10b981;
    border-color: rgba(16, 185, 129, 0.35);
  }
  .gauge-container {
    margin: 12px 0 14px 0;
    background: rgba(128, 128, 128, 0.14);
    border-radius: 12px;
    padding: 10px 12px;
  }
  .gauge-labels {
    display: flex;
    justify-content: space-between;
    font-size: 0.78rem;
    margin-bottom: 6px;
    color: var(--secondary-text-color);
  }
  .gauge-Labels-strong {
    color: var(--primary-text-color);
    font-weight: 600;
  }
  .bar-track {
    position: relative;
    height: 10px;
    border-radius: 999px;
    background: rgba(128, 128, 128, 0.22);
    overflow: hidden;
  }
  .bar-fill-total {
    position: absolute;
    top: 0;
    left: 0;
    height: 100%;
    border-radius: 999px;
    transition: width 0.4s ease;
  }
  .bar-fill-natural {
    position: absolute;
    top: 0;
    left: 0;
    height: 100%;
    border-radius: 999px;
    background: rgba(56, 189, 248, 0.75);
    transition: width 0.4s ease;
  }
  .scenes-grid {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin: 12px 0;
  }
  .scene-chip {
    flex: 1 1 auto;
    min-width: 92px;
    text-align: center;
    padding: 8px 12px;
    border-radius: 12px;
    font-size: 0.83rem;
    font-weight: 500;
    cursor: pointer;
    user-select: none;
    border: 1px solid rgba(128, 128, 128, 0.24);
    background: rgba(128, 128, 128, 0.09);
    color: var(--primary-text-color);
    transition: all 0.15s ease;
  }
  .scene-chip:hover {
    background: rgba(128, 128, 128, 0.18);
  }
  .scene-chip.active {
    background: var(--primary-color, #3b82f6);
    color: #ffffff;
    border-color: var(--primary-color, #3b82f6);
    font-weight: 600;
    box-shadow: 0 2px 8px rgba(59, 130, 246, 0.32);
  }
  .tune-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    margin-top: 12px;
    padding-top: 12px;
    border-top: 1px solid rgba(128, 128, 128, 0.16);
    flex-wrap: wrap;
  }
  .nudge-group {
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .btn-action {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    padding: 6px 11px;
    border-radius: 10px;
    font-size: 0.79rem;
    font-weight: 500;
    cursor: pointer;
    border: 1px solid rgba(128, 128, 128, 0.26);
    background: rgba(128, 128, 128, 0.1);
    color: var(--primary-text-color);
    transition: all 0.15s ease;
  }
  .btn-action:hover {
    background: rgba(128, 128, 128, 0.22);
  }
  .btn-action:active {
    transform: scale(0.97);
  }
  .drawer {
    margin-top: 12px;
    padding: 12px;
    border-radius: 12px;
    background: rgba(128, 128, 128, 0.09);
    border: 1px solid rgba(128, 128, 128, 0.2);
    font-size: 0.82rem;
  }
  .drawer h4 {
    margin: 0 0 8px 0;
    font-size: 0.88rem;
  }
  .input-row {
    display: flex;
    gap: 8px;
    align-items: center;
    margin-top: 6px;
  }
  .input-row input {
    flex: 1;
    padding: 6px 10px;
    border-radius: 8px;
    border: 1px solid rgba(128, 128, 128, 0.35);
    background: var(--card-background-color, #fff);
    color: var(--primary-text-color);
  }
  .entity-check-list {
    display: flex;
    flex-direction: column;
    gap: 5px;
    max-height: 180px;
    overflow-y: auto;
    margin-top: 6px;
  }
  .entity-check-item {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 4px 6px;
    border-radius: 6px;
    background: rgba(128, 128, 128, 0.07);
  }
  .room-list-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 10px 12px;
    border-radius: 12px;
    background: rgba(128, 128, 128, 0.09);
    margin-bottom: 8px;
    gap: 10px;
    flex-wrap: wrap;
  }
`;

class PerceptiveRoomCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = {};
    this._hass = null;
    this._showCaptureDrawer = false;
    this._showSettingsDrawer = false;
    this._liveCompassHeading = null;
    this._compassListener = null;
  }

  static getConfigElement() {
    return document.createElement("perceptive-room-card-editor");
  }

  static getStubConfig(hass) {
    const rooms = findAllRoomSensors(hass);
    return {
      room: rooms.length > 0 ? rooms[0].attributes.room_id : "",
      show_tuning: true,
    };
  }

  setConfig(config) {
    if (!config) {
      throw new Error("Invalid configuration");
    }
    this._config = config;
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  disconnectedCallback() {
    this._stopCompass();
  }

  _startCompass() {
    if (this._compassListener) return;
    this._compassListener = (ev) => {
      let heading = null;
      if (typeof ev.webkitCompassHeading === "number") {
        heading = ev.webkitCompassHeading;
      } else if (typeof ev.alpha === "number") {
        heading = (360 - ev.alpha) % 360;
      }
      if (heading !== null) {
        this._liveCompassHeading = Math.round(heading);
        const el = this.shadowRoot.querySelector("#live-compass-val");
        if (el) {
          el.textContent = compassDegreesToCardinal(this._liveCompassHeading);
        }
      }
    };
    if (
      typeof DeviceOrientationEvent !== "undefined" &&
      typeof DeviceOrientationEvent.requestPermission === "function"
    ) {
      DeviceOrientationEvent.requestPermission()
        .then((res) => {
          if (res === "granted") {
            window.addEventListener("deviceorientation", this._compassListener, true);
          }
        })
        .catch(() => {});
    } else {
      window.addEventListener("deviceorientationabsolute", this._compassListener, true);
      window.addEventListener("deviceorientation", this._compassListener, true);
    }
  }

  _stopCompass() {
    if (this._compassListener) {
      window.removeEventListener("deviceorientationabsolute", this._compassListener, true);
      window.removeEventListener("deviceorientation", this._compassListener, true);
      this._compassListener = null;
    }
  }

  _callService(service, data) {
    if (!this._hass) return;
    this._hass.callService(DOMAIN, service, data);
  }

  _render() {
    if (!this.shadowRoot) return;
    const sensor = findRoomSensor(this._hass, this._config.room);
    if (!sensor) {
      this.shadowRoot.innerHTML = `
        <style>${SHARED_STYLES}</style>
        <ha-card>
          <div class="room-title">✨ Perceptive Lighting Room Card</div>
          <div class="subtitle" style="margin-top:6px;">
            ${
              this._config.room
                ? `Waiting for room "${this._config.room}"...`
                : "Select a configured room in the card editor."
            }
          </div>
        </ha-card>
      `;
      return;
    }

    const attr = sensor.attributes;
    const roomId = attr.room_id;
    const roomName = this._config.title || attr.room_name || roomId;
    const totalPct = Math.round(Number(sensor.state) || 0);
    const naturalPct = Math.round(Number(attr.natural_perceptive_illuminance_pct) || 0);
    const deficitPct = Math.round(Number(attr.lighting_deficit_pct) || 0);
    const pState = attr.perceptive_state || "dim";
    const isLocked = Boolean(attr.automation_locked);
    const isOverride = Boolean(attr.manual_override_active);
    const isOccupied = Boolean(attr.is_occupied);
    const activeSceneId = attr.active_scene_id || "default";
    const activeActivity = attr.active_activity || "relaxing";
    const scenesMap = attr.available_scenes || {};
    const barColor = STATE_COLORS[pState] || "#3b82f6";

    let statusPillHtml = "";
    if (isLocked) {
      statusPillHtml = `<div class="pill-badge locked" id="btn-toggle-lock" title="Tap to unlock room automation">🔒 Locked</div>`;
    } else if (isOverride) {
      statusPillHtml = `<div class="pill-badge override" id="btn-clear-override" title="Manual change detected. Tap to resume automatic control">✋ Manual Override</div>`;
    } else {
      statusPillHtml = `<div class="pill-badge auto" id="btn-toggle-lock" title="Tap to lock room automation">✨ Auto (${isOccupied ? "Occupied" : "Vacant"})</div>`;
    }

    const sceneChipsHtml = Object.entries(scenesMap)
      .map(([sid, label]) => {
        const isActive =
          sid === activeSceneId ||
          (activeSceneId === "default" && sid === `activity_${activeActivity}`);
        const cleanLabel = String(label).replace(/^Scene:\s*/i, "");
        return `<div class="scene-chip ${isActive ? "active" : ""}" data-scene="${sid}">${cleanLabel}</div>`;
      })
      .join("");

    const showTuning = this._config.show_tuning !== false;
    const nudgeVal = attr.feel_nudge_day_pct || 0;

    let captureDrawerHtml = "";
    if (this._showCaptureDrawer) {
      captureDrawerHtml = `
        <div class="drawer">
          <h4>📸 Save Current Room Lights as Adaptive Scene</h4>
          <div style="color:var(--secondary-text-color);">
            Captures each light's current brightness & state at the current ${deficitPct}% lighting deficit so it scales naturally at any time of day.
          </div>
          <div class="input-row">
            <input type="text" id="input-scene-name" placeholder="e.g. Reading, Film, Board Games..." />
            <button class="btn-action" id="btn-save-scene">Save Scene</button>
          </div>
        </div>
      `;
    }

    let settingsDrawerHtml = "";
    if (this._showSettingsDrawer) {
      const allArea = attr.all_area_entities || {};
      const allFlat = [
        ...(allArea.illuminance_sensors || []),
        ...(allArea.presence_sensors || []),
        ...(allArea.media_players || []),
        ...(allArea.lights || []),
      ];
      const excludedSet = new Set(attr.excluded_entities || []);
      const entityCheckboxes = allFlat
        .map((eid) => {
          const isIncluded = !excludedSet.has(eid);
          return `
            <label class="entity-check-item">
              <span>${eid}</span>
              <input type="checkbox" class="chk-include-entity" data-eid="${eid}" ${
                isIncluded ? "checked" : ""
              } />
            </label>
          `;
        })
        .join("");

      settingsDrawerHtml = `
        <div class="drawer">
          <h4>🧭 Window Compass Calibration</h4>
          <div style="color:var(--secondary-text-color);margin-bottom:6px;">
            Current window azimuth: <b>${compassDegreesToCardinal(attr.window_azimuth)}</b>
            ${attr.direct_sun_in_window ? "☀️ Direct sun in window" : ""}
          </div>
          <div class="input-row">
            <span>Phone Heading: <b id="live-compass-val">${
              this._liveCompassHeading !== null
                ? compassDegreesToCardinal(this._liveCompassHeading)
                : "Point phone at window..."
            }</b></span>
            <button class="btn-action" id="btn-save-compass">Set Window Direction</button>
          </div>

          <h4 style="margin-top:14px;">📦 Included Area Entities (Uncheck to Blacklist)</h4>
          <div class="entity-check-list">
            ${entityCheckboxes || "<div>No entities found in Area.</div>"}
          </div>
          <div class="input-row" style="justify-content:flex-end;margin-top:8px;">
            <button class="btn-action" id="btn-save-exclusions">Save Entity List</button>
          </div>
        </div>
      `;
    }

    this.shadowRoot.innerHTML = `
      <style>${SHARED_STYLES}</style>
      <ha-card>
        <div class="header-row">
          <div class="title-block">
            <div class="room-title">
              <span>${roomName}</span>
            </div>
            <div class="subtitle">
              <span>State: <b>${pState.toUpperCase()}</b></span>
              <span>• Raw: ${attr.raw_lux} lx (Target ${attr.target_comfort_lux} lx)</span>
              <span>• Deficit: ${deficitPct}%</span>
            </div>
          </div>
          <div class="badges-row">
            ${statusPillHtml}
            <div class="pill-badge" id="btn-toggle-settings" title="Window Compass & Area Exclusions">⚙️</div>
          </div>
        </div>

        <div class="gauge-container">
          <div class="gauge-labels">
            <span>Perceptive Illuminance: <span class="gauge-Labels-strong">${totalPct}%</span></span>
            <span>Daylight Only: <span class="gauge-Labels-strong">${naturalPct}%</span></span>
          </div>
          <div class="bar-track">
            <div class="bar-fill-total" style="width:${totalPct}%;background:${barColor};"></div>
            <div class="bar-fill-natural" style="width:${naturalPct}%;" title="Natural Daylight Contribution (${naturalPct}%)"></div>
          </div>
        </div>

        <div class="scenes-grid">
          ${sceneChipsHtml}
        </div>

        ${
          showTuning
            ? `
          <div class="tune-row">
            <div class="nudge-group">
              <button class="btn-action" id="btn-nudge-darker" title="Nudge room target lower (-20% target lux)">🔉 Too Bright (-)</button>
              <button class="btn-action" id="btn-nudge-lighter" title="Nudge room target higher (+20% target lux)">🔆 Needs More Light (+)</button>
              <button class="btn-action" id="btn-feels-dark-now" title="One-tap teach: It feels dark right now (turn lights ON)">🌑 Feels Dark Now</button>
              <button class="btn-action" id="btn-feels-bright-now" title="One-tap teach: It feels bright enough right now (turn lights OFF)">☀️ Bright Enough Now</button>
            </div>
            <button class="btn-action" id="btn-toggle-capture">📸 Capture Scene</button>
          </div>
        `
            : ""
        }

        ${captureDrawerHtml}
        ${settingsDrawerHtml}
      </ha-card>
    `;

    // Bind interactive events
    const lockBtn = this.shadowRoot.querySelector("#btn-toggle-lock");
    if (lockBtn) {
      lockBtn.addEventListener("click", () => {
        this._callService(isLocked ? "unlock_room" : "lock_room", {
          room_id: roomId,
        });
      });
    }

    const overrideBtn = this.shadowRoot.querySelector("#btn-clear-override");
    if (overrideBtn) {
      overrideBtn.addEventListener("click", () => {
        this._callService("apply_adaptive_scene", {
          room_id: roomId,
          scene: activeSceneId,
        });
      });
    }

    this.shadowRoot.querySelectorAll(".scene-chip").forEach((chip) => {
      chip.addEventListener("click", () => {
        const sceneKey = chip.getAttribute("data-scene");
        this._callService("apply_adaptive_scene", {
          room_id: roomId,
          scene: sceneKey,
        });
      });
    });

    const btnDarker = this.shadowRoot.querySelector("#btn-nudge-darker");
    if (btnDarker) {
      btnDarker.addEventListener("click", () => {
        this._callService("nudge_feel", {
          room_id: roomId,
          direction: "too_bright",
        });
      });
    }

    const btnLighter = this.shadowRoot.querySelector("#btn-nudge-lighter");
    if (btnLighter) {
      btnLighter.addEventListener("click", () => {
        this._callService("nudge_feel", {
          room_id: roomId,
          direction: "more_light",
        });
      });
    }

    const btnDarkNow = this.shadowRoot.querySelector("#btn-feels-dark-now");
    if (btnDarkNow) {
      btnDarkNow.addEventListener("click", () => {
        this._callService("calibrate_feels_dark_now", { room_id: roomId });
      });
    }

    const btnBrightNow = this.shadowRoot.querySelector("#btn-feels-bright-now");
    if (btnBrightNow) {
      btnBrightNow.addEventListener("click", () => {
        this._callService("calibrate_feels_bright_now", { room_id: roomId });
      });
    }

    const btnCaptureToggle = this.shadowRoot.querySelector("#btn-toggle-capture");
    if (btnCaptureToggle) {
      btnCaptureToggle.addEventListener("click", () => {
        this._showCaptureDrawer = !this._showCaptureDrawer;
        this._render();
      });
    }

    const btnSaveScene = this.shadowRoot.querySelector("#btn-save-scene");
    if (btnSaveScene) {
      btnSaveScene.addEventListener("click", () => {
        const inp = this.shadowRoot.querySelector("#input-scene-name");
        const name = inp ? inp.value.trim() : "";
        if (name) {
          this._callService("capture_adaptive_scene", {
            room_id: roomId,
            name: name,
          });
          this._showCaptureDrawer = false;
          this._render();
        }
      });
    }

    const btnSettingsToggle = this.shadowRoot.querySelector("#btn-toggle-settings");
    if (btnSettingsToggle) {
      btnSettingsToggle.addEventListener("click", () => {
        this._showSettingsDrawer = !this._showSettingsDrawer;
        if (this._showSettingsDrawer) {
          this._startCompass();
        } else {
          this._stopCompass();
        }
        this._render();
      });
    }

    const btnSaveCompass = this.shadowRoot.querySelector("#btn-save-compass");
    if (btnSaveCompass) {
      btnSaveCompass.addEventListener("click", () => {
        const az =
          this._liveCompassHeading !== null
            ? this._liveCompassHeading
            : Number(prompt("Enter Window Compass Azimuth (0-360°):", "180"));
        if (!isNaN(az)) {
          this._callService("set_window_compass", {
            room_id: roomId,
            azimuth: az,
          });
        }
      });
    }

    const btnSaveExclusions = this.shadowRoot.querySelector("#btn-save-exclusions");
    if (btnSaveExclusions) {
      btnSaveExclusions.addEventListener("click", () => {
        const excluded = [];
        this.shadowRoot.querySelectorAll(".chk-include-entity").forEach((chk) => {
          if (!chk.checked) {
            excluded.push(chk.getAttribute("data-eid"));
          }
        });
        this._callService("update_room_exclusions", {
          room_id: roomId,
          excluded_entities: excluded,
        });
        this._showSettingsDrawer = false;
        this._render();
      });
    }
  }

  getCardSize() {
    return 3;
  }
}

class PerceptiveRoomCardEditor extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = {};
    this._hass = null;
  }

  setConfig(config) {
    this._config = config || {};
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  _render() {
    if (!this.shadowRoot || !this._hass) return;
    const rooms = findAllRoomSensors(this._hass);
    const optionsHtml = rooms
      .map((st) => {
        const rid = st.attributes.room_id;
        const rname = st.attributes.room_name || rid;
        const selected = this._config.room === rid ? "selected" : "";
        return `<option value="${rid}" ${selected}>${rname}</option>`;
      })
      .join("");

    this.shadowRoot.innerHTML = `
      <style>
        .editor-form {
          display: flex;
          flex-direction: column;
          gap: 12px;
          padding: 8px 0;
        }
        label {
          display: flex;
          flex-direction: column;
          gap: 4px;
          font-size: 0.9rem;
        }
        select, input[type="text"] {
          padding: 8px;
          border-radius: 8px;
          border: 1px solid rgba(128,128,128,0.4);
        }
      </style>
      <div class="editor-form">
        <label>
          <span>Select Perceptive Lighting Room</span>
          <select id="select-room">
            <option value="">-- Automatic / First Room --</option>
            ${optionsHtml}
          </select>
        </label>
        <label>
          <span>Custom Card Title (Optional)</span>
          <input type="text" id="input-title" value="${this._config.title || ""}" placeholder="Defaults to Room Name" />
        </label>
      </div>
    `;

    this.shadowRoot.querySelector("#select-room").addEventListener("change", (ev) => {
      this._updateConfig({ room: ev.target.value });
    });
    this.shadowRoot.querySelector("#input-title").addEventListener("change", (ev) => {
      this._updateConfig({ title: ev.target.value });
    });
  }

  _updateConfig(patch) {
    this._config = { ...this._config, ...patch };
    this.dispatchEvent(
      new CustomEvent("config-changed", {
        detail: { config: this._config },
        bubbles: true,
        composed: true,
      })
    );
  }
}

class PerceptiveHomeCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = {};
    this._hass = null;
  }

  static getStubConfig() {
    return { title: "Perceptive Lighting — Home Overview" };
  }

  setConfig(config) {
    this._config = config || {};
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  _render() {
    if (!this.shadowRoot || !this._hass) return;
    const outdoor = findHubOutdoorSensor(this._hass);
    const rooms = findAllRoomSensors(this._hass);

    const outLux = outdoor ? Math.round(Number(outdoor.state) || 0) : 0;
    const outAttr = outdoor ? outdoor.attributes : {};
    const skyQuality = (outAttr.sky_quality || "diffuse").replace("_", " ");
    const sunEl = outAttr.solar_elevation ?? "--";
    const diffuseFactor = outAttr.diffuse_sky_factor ?? 1.0;

    const roomRowsHtml = rooms
      .map((st) => {
        const a = st.attributes;
        const rid = a.room_id;
        const rname = a.room_name || rid;
        const pct = Math.round(Number(st.state) || 0);
        const natPct = Math.round(Number(a.natural_perceptive_illuminance_pct) || 0);
        const defPct = Math.round(Number(a.lighting_deficit_pct) || 0);
        const pState = a.perceptive_state || "dim";
        const isLocked = Boolean(a.automation_locked);
        const isOcc = Boolean(a.is_occupied);

        return `
          <div class="room-list-row">
            <div>
              <div style="font-weight:600;font-size:0.95rem;">
                ${rname}
                <span style="font-size:0.75rem;font-weight:400;color:var(--secondary-text-color);">
                  (${isOcc ? "Occupied" : "Vacant"} • ${a.active_activity})
                </span>
              </div>
              <div style="font-size:0.78rem;color:var(--secondary-text-color);">
                Perceptive: <b>${pct}%</b> (Daylight ${natPct}%) • Deficit: <b>${defPct}%</b> • ${pState.toUpperCase()}
              </div>
            </div>
            <div class="badges-row">
              <div class="pill-badge ${isLocked ? "locked" : "auto"}" data-lock-room="${rid}" data-locked="${isLocked}">
                ${isLocked ? "🔒 Locked" : "✨ Auto"}
              </div>
            </div>
          </div>
        `;
      })
      .join("");

    this.shadowRoot.innerHTML = `
      <style>${SHARED_STYLES}</style>
      <ha-card>
        <div class="header-row">
          <div class="title-block">
            <div class="room-title">${this._config.title || "🌤️ Perceptive Lighting — Home"}</div>
            <div class="subtitle">
              <span>Outdoor Sky: <b>${outLux} lx</b> (${skyQuality})</span>
              <span>• Diffusion: ${diffuseFactor}x</span>
              <span>• Sun Elevation: ${sunEl}°</span>
            </div>
          </div>
          <button class="btn-action" id="btn-relearn-history" title="Re-analyze Recorder history for all sensors">🔄 Re-Learn</button>
        </div>

        <div>
          ${roomRowsHtml || `<div class="subtitle">No rooms configured yet.</div>`}
        </div>
      </ha-card>
    `;

    this.shadowRoot.querySelectorAll("[data-lock-room]").forEach((el) => {
      el.addEventListener("click", () => {
        const rid = el.getAttribute("data-lock-room");
        const currentlyLocked = el.getAttribute("data-locked") === "true";
        this._hass.callService(
          DOMAIN,
          currentlyLocked ? "unlock_room" : "lock_room",
          { room_id: rid }
        );
      });
    });

    const btnRelearn = this.shadowRoot.querySelector("#btn-relearn-history");
    if (btnRelearn) {
      btnRelearn.addEventListener("click", () => {
        this._hass.callService(DOMAIN, "relearn_history", {});
      });
    }
  }

  getCardSize() {
    return 4;
  }
}

if (!customElements.get("perceptive-room-card")) {
  customElements.define("perceptive-room-card", PerceptiveRoomCard);
}
if (!customElements.get("perceptive-room-card-editor")) {
  customElements.define("perceptive-room-card-editor", PerceptiveRoomCardEditor);
}
if (!customElements.get("perceptive-home-card")) {
  customElements.define("perceptive-home-card", PerceptiveHomeCard);
}

window.customCards = window.customCards || [];
if (!window.customCards.some((c) => c.type === "perceptive-room-card")) {
  window.customCards.push({
    type: "perceptive-room-card",
    name: "Perceptive Lighting: Room Card",
    description:
      "Adaptive Scenes, Perceptive Comfort Gauge, Micro-Step Feel Tuning, Window Compass & Exclusions for a room.",
    preview: true,
  });
}
if (!window.customCards.some((c) => c.type === "perceptive-home-card")) {
  window.customCards.push({
    type: "perceptive-home-card",
    name: "Perceptive Lighting: Whole-Home Card",
    description:
      "Whole-home Perceptive Illuminance overview, Outdoor Sky Adaptation metrics, and quick Room Locks.",
    preview: true,
  });
}
