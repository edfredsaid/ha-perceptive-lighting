/**
 * Perceptive Lighting v2.0.0 — Custom Sidebar Panel & Lovelace Cards
 *
 * Includes:
 * 1. <perceptive-lighting-panel> — Full-screen Home Assistant Sidebar Studio (/perceptive-lighting)
 *    - Live "What Is Going On & Why" plain-English diagnostics for every room
 *    - Direct Control (+ / -) with 1% minimum floor & progressive one-by-one light shedding
 *    - Reorderable Light Priority List (1st to shed -> last to keep on at 1%)
 *    - Per-Time-of-Day Regime Matrix (Morning, Day, Evening, Overnight):
 *      - Default Occupied Scene (Scene A)
 *      - Off-State / Unoccupied Scene Override (Scene B or Off)
 *      - Turn-On Lux Threshold, Target Brightness %, Target Total Room Lux, & Competing Models
 *    - Per-Regime Entity Scene Triggers (e.g. TV playing in Day -> Scene X, TV playing in Evening -> Scene Y)
 *    - Global Time-of-Day Schedule & Factors Editor (Hybrid / Clock / Solar, Gloom Shift, Cross-Fade Window)
 *    - Room & Whole-Home Reset controls
 * 2. <perceptive-room-card> — Compact Lovelace Card for a single room
 * 3. <perceptive-home-card> — Whole-home overview Lovelace Card
 */

const DOMAIN = "perceptive_lighting";

const REGIME_META = {
  morning: { label: "Morning", icon: "🌅", color: "#f59e0b" },
  day: { label: "Day", icon: "☀️", color: "#38bdf8" },
  evening: { label: "Evening", icon: "🌆", color: "#a855f7" },
  overnight: { label: "Overnight", icon: "🌙", color: "#6366f1" },
};

const ILLUM_MODEL_OPTIONS = {
  weber_fechner: "Weber-Fechner Log (Recommended)",
  stevens_power: "Stevens Power Law",
  naka_rushton: "Naka-Rushton Photoreceptor",
  cuttle_mrse: "Cuttle Ambient MRSE",
  cie_daylight: "CIE Daylight Factor",
};

const WEATHER_MODEL_OPTIONS = {
  cave_contrast: "Cave Effect Contrast Boost",
  gloom_compensation: "Gloomy / Overcast Uplift",
  solar_altitude: "Solar Altitude Penetration",
  weather_neutral: "Weather Neutral (1.0x)",
};

const SEASON_MODEL_OPTIONS = {
  bioclimatic_combined: "Bioclimatic (Season + Temp)",
  photoperiod_seasonal: "Winter Photoperiod Boost",
  thermal_hue_heat: "Thermal Hue-Heat Compensation",
  season_neutral: "Season Neutral (1.0x)",
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
  return Object.values(hass.states)
    .filter(
      (st) =>
        st.entity_id.startsWith("sensor.") &&
        st.attributes &&
        st.attributes.room_id &&
        st.attributes.available_scenes !== undefined
    )
    .sort((a, b) =>
      String(a.attributes.room_name || a.attributes.room_id).localeCompare(
        String(b.attributes.room_name || b.attributes.room_id)
      )
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

function escapeHtml(str) {
  return String(str ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function formatHourDecimal(h) {
  const num = Number(h) || 0;
  const hrs = Math.floor(num);
  const mins = Math.round((num - hrs) * 60);
  return `${String(hrs).padStart(2, "0")}:${String(mins).padStart(2, "0")}`;
}

/* ==========================================================================
 * 1. FULL-SCREEN SIDEBAR PANEL: <perceptive-lighting-panel>
 * ========================================================================== */

const PANEL_STYLES = `
  :host {
    display: block;
    min-height: 100vh;
    background: var(--primary-background-color, #111827);
    color: var(--primary-text-color, #f3f4f6);
    font-family: var(--paper-font-body1_-_font-family, system-ui, -apple-system, sans-serif);
    box-sizing: border-box;
  }
  .panel-wrap {
    max-width: 1360px;
    margin: 0 auto;
    padding: 16px 20px 48px 20px;
  }
  .topbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    padding-bottom: 14px;
    border-bottom: 1px solid rgba(128, 128, 128, 0.22);
    flex-wrap: wrap;
  }
  .topbar-left {
    display: flex;
    align-items: center;
    gap: 12px;
  }
  .menu-btn {
    background: rgba(128, 128, 128, 0.15);
    border: 1px solid rgba(128, 128, 128, 0.3);
    color: var(--primary-text-color);
    border-radius: 10px;
    padding: 8px 12px;
    font-size: 1.05rem;
    cursor: pointer;
  }
  .brand-title {
    font-size: 1.35rem;
    font-weight: 700;
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .brand-version {
    font-size: 0.72rem;
    font-weight: 600;
    padding: 2px 8px;
    border-radius: 999px;
    background: rgba(59, 130, 246, 0.2);
    color: #60a5fa;
    border: 1px solid rgba(59, 130, 246, 0.4);
  }
  .outdoor-strip {
    margin-top: 14px;
    padding: 12px 16px;
    border-radius: 14px;
    background: var(--ha-card-background, var(--card-background-color, #1e293b));
    border: 1px solid rgba(128, 128, 128, 0.2);
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 14px;
    flex-wrap: wrap;
  }
  .regime-pills {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }
  .regime-pill {
    padding: 4px 10px;
    border-radius: 999px;
    font-size: 0.78rem;
    font-weight: 600;
    background: rgba(128, 128, 128, 0.14);
    border: 1px solid rgba(128, 128, 128, 0.25);
  }
  .regime-pill.active {
    background: rgba(59, 130, 246, 0.22);
    border-color: #3b82f6;
    color: #93c5fd;
  }
  .tabs-bar {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-top: 16px;
    overflow-x: auto;
    padding-bottom: 4px;
  }
  .tab-btn {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    padding: 10px 16px;
    border-radius: 12px;
    font-size: 0.9rem;
    font-weight: 600;
    cursor: pointer;
    border: 1px solid rgba(128, 128, 128, 0.25);
    background: var(--ha-card-background, var(--card-background-color, #1e293b));
    color: var(--primary-text-color);
    white-space: nowrap;
    transition: all 0.15s ease;
  }
  .tab-btn:hover {
    filter: brightness(1.1);
  }
  .tab-btn.active {
    background: var(--primary-color, #3b82f6);
    color: #ffffff;
    border-color: var(--primary-color, #3b82f6);
    box-shadow: 0 3px 10px rgba(59, 130, 246, 0.35);
  }
  .dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    display: inline-block;
  }
  .dot.occ {
    background: #10b981;
    box-shadow: 0 0 6px #10b981;
  }
  .dot.vac {
    background: #64748b;
  }
  .section-card {
    margin-top: 16px;
    padding: 18px;
    border-radius: 16px;
    background: var(--ha-card-background, var(--card-background-color, #1e293b));
    border: 1px solid rgba(128, 128, 128, 0.2);
  }
  .section-title {
    font-size: 1.05rem;
    font-weight: 700;
    margin: 0 0 6px 0;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    flex-wrap: wrap;
  }
  .section-desc {
    font-size: 0.82rem;
    color: var(--secondary-text-color, #9ca3af);
    margin-bottom: 14px;
    line-height: 1.45;
  }
  .reason-banner {
    padding: 14px 16px;
    border-radius: 12px;
    background: rgba(59, 130, 246, 0.12);
    border: 1px solid rgba(59, 130, 246, 0.35);
    font-size: 0.92rem;
    line-height: 1.5;
    margin-bottom: 14px;
  }
  .metrics-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(175px, 1fr));
    gap: 10px;
    margin-bottom: 14px;
  }
  .metric-box {
    padding: 10px 12px;
    border-radius: 12px;
    background: rgba(128, 128, 128, 0.09);
    border: 1px solid rgba(128, 128, 128, 0.18);
  }
  .metric-label {
    font-size: 0.74rem;
    color: var(--secondary-text-color, #9ca3af);
    text-transform: uppercase;
    letter-spacing: 0.03em;
  }
  .metric-val {
    font-size: 1.15rem;
    font-weight: 700;
    margin-top: 3px;
  }
  .metric-sub {
    font-size: 0.76rem;
    color: var(--secondary-text-color, #9ca3af);
    margin-top: 2px;
  }
  .btn-row {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    align-items: center;
  }
  .btn {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 8px 14px;
    border-radius: 10px;
    font-size: 0.84rem;
    font-weight: 600;
    cursor: pointer;
    border: 1px solid rgba(128, 128, 128, 0.3);
    background: rgba(128, 128, 128, 0.12);
    color: var(--primary-text-color);
    transition: all 0.15s ease;
  }
  .btn:hover {
    background: rgba(128, 128, 128, 0.24);
  }
  .btn.primary {
    background: var(--primary-color, #3b82f6);
    color: #ffffff;
    border-color: var(--primary-color, #3b82f6);
  }
  .btn.success {
    background: rgba(16, 185, 129, 0.2);
    color: #34d399;
    border-color: rgba(16, 185, 129, 0.45);
  }
  .btn.warn {
    background: rgba(245, 158, 11, 0.2);
    color: #fbbf24;
    border-color: rgba(245, 158, 11, 0.45);
  }
  .btn.danger {
    background: rgba(239, 68, 68, 0.18);
    color: #f87171;
    border-color: rgba(239, 68, 68, 0.4);
  }
  .regime-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(270px, 1fr));
    gap: 14px;
  }
  .regime-card {
    padding: 14px;
    border-radius: 14px;
    background: rgba(128, 128, 128, 0.08);
    border: 1px solid rgba(128, 128, 128, 0.22);
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  .regime-card.active-regime {
    border-color: #3b82f6;
    box-shadow: 0 0 0 1px rgba(59, 130, 246, 0.35);
  }
  .regime-card-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    font-weight: 700;
    font-size: 0.96rem;
    padding-bottom: 6px;
    border-bottom: 1px solid rgba(128, 128, 128, 0.18);
  }
  .field-group {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .field-group label {
    font-size: 0.76rem;
    font-weight: 600;
    color: var(--secondary-text-color, #9ca3af);
  }
  .field-row-2 {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 8px;
  }
  input[type="text"],
  input[type="number"],
  select {
    width: 100%;
    box-sizing: border-box;
    padding: 7px 10px;
    border-radius: 8px;
    border: 1px solid rgba(128, 128, 128, 0.35);
    background: var(--primary-background-color, #0f172a);
    color: var(--primary-text-color, #f3f4f6);
    font-size: 0.83rem;
  }
  .chk-row {
    display: flex;
    align-items: center;
    gap: 7px;
    font-size: 0.79rem;
    cursor: pointer;
  }
  .light-order-list {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .light-order-item {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    padding: 10px 12px;
    border-radius: 10px;
    background: rgba(128, 128, 128, 0.09);
    border: 1px solid rgba(128, 128, 128, 0.2);
    flex-wrap: wrap;
  }
  .light-order-item.shed-now {
    border-color: rgba(245, 158, 11, 0.5);
    background: rgba(245, 158, 11, 0.1);
  }
  .badge-cat {
    padding: 2px 8px;
    border-radius: 999px;
    font-size: 0.72rem;
    font-weight: 600;
    background: rgba(99, 102, 241, 0.18);
    color: #a5b4fc;
    border: 1px solid rgba(99, 102, 241, 0.35);
  }
  .trigger-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    padding: 10px 12px;
    border-radius: 10px;
    background: rgba(128, 128, 128, 0.09);
    border: 1px solid rgba(128, 128, 128, 0.2);
    margin-bottom: 8px;
    flex-wrap: wrap;
  }
  .toast {
    position: fixed;
    bottom: 20px;
    right: 20px;
    padding: 10px 16px;
    border-radius: 10px;
    background: #10b981;
    color: #fff;
    font-weight: 600;
    font-size: 0.86rem;
    box-shadow: 0 4px 14px rgba(0, 0, 0, 0.4);
    z-index: 9999;
  }
`;

class PerceptiveLightingPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._narrow = false;
    this._activeTab = null; // room_id or "_hub_settings"
    this._toastMsg = null;
    this._toastTimer = null;
    this._lastStateSignature = "";
  }

  set narrow(val) {
    this._narrow = Boolean(val);
  }

  set panel(val) {
    this._panel = val;
  }

  set route(val) {
    this._route = val;
  }

  set hass(hass) {
    this._hass = hass;
    if (!hass) return;

    // Do not clobber the DOM while the user is actively typing or editing a select/input
    const activeEl = this.shadowRoot ? this.shadowRoot.activeElement : null;
    if (
      activeEl &&
      (activeEl.tagName === "INPUT" ||
        activeEl.tagName === "SELECT" ||
        activeEl.tagName === "TEXTAREA")
    ) {
      return;
    }

    const sig = this._computeStateSignature(hass);
    if (sig !== this._lastStateSignature) {
      this._lastStateSignature = sig;
      this._render();
    }
  }

  _computeStateSignature(hass) {
    const out = findHubOutdoorSensor(hass);
    const rooms = findAllRoomSensors(hass);
    if (! this._activeTab && rooms.length > 0) {
      this._activeTab = rooms[0].attributes.room_id;
    }
    const parts = [
      this._activeTab,
      out ? `${out.state}:${out.last_updated}` : "no_out",
    ];
    for (const r of rooms) {
      parts.push(
        `${r.attributes.room_id}:${r.state}:${r.last_updated}:${r.attributes.decision_reason}:${r.attributes.recommended_brightness_pct}:${r.attributes.active_shed_count}`
      );
    }
    return parts.join("|");
  }

  _showToast(msg) {
    this._toastMsg = msg;
    if (this._toastTimer) clearTimeout(this._toastTimer);
    this._render();
    this._toastTimer = setTimeout(() => {
      this._toastMsg = null;
      this._render();
    }, 3000);
  }

  _callService(service, data, toastText) {
    if (!this._hass) return;
    this._hass.callService(DOMAIN, service, data);
    if (toastText) {
      this._showToast(toastText);
    }
  }

  _render() {
    if (!this.shadowRoot || !this._hass) return;

    const outdoor = findHubOutdoorSensor(this._hass);
    const rooms = findAllRoomSensors(this._hass);
    if (!this._activeTab) {
      this._activeTab = rooms.length > 0 ? rooms[0].attributes.room_id : "_hub_settings";
    }

    const outAttr = outdoor ? outdoor.attributes : {};
    const outLux = outdoor ? Math.round(Number(outdoor.state) || 0) : 0;
    const regimeWeights = outAttr.regime_weights || {
      morning: 0,
      day: 1,
      evening: 0,
      overnight: 0,
    };
    const regimeExplanation =
      outAttr.regime_explanation || "Time-of-day regime active";

    const regimePillsHtml = Object.entries(REGIME_META)
      .map(([rkey, meta]) => {
        const wPct = Math.round((Number(regimeWeights[rkey]) || 0) * 100);
        const isAct = wPct > 0;
        return `<span class="regime-pill ${isAct ? "active" : ""}">${meta.icon} ${meta.label}: ${wPct}%</span>`;
      })
      .join("");

    const tabsHtml = [
      ...rooms.map((st) => {
        const a = st.attributes;
        const rid = a.room_id;
        const rname = a.room_name || rid;
        const isOcc = Boolean(a.is_occupied);
        const isAct = this._activeTab === rid;
        const brPct = Math.round(Number(a.recommended_brightness_pct) || 0);
        return `
          <button class="tab-btn ${isAct ? "active" : ""}" data-tab="${escapeHtml(rid)}">
            <span class="dot ${isOcc ? "occ" : "vac"}"></span>
            <span>${escapeHtml(rname)}</span>
            <span style="font-size:0.75rem;opacity:0.85;">(${a.raw_lux} lx • ${brPct}%)</span>
          </button>
        `;
      }),
      `<button class="tab-btn ${
        this._activeTab === "_hub_settings" ? "active" : ""
      }" data-tab="_hub_settings">
        🌍 Time-of-Day & Hub Settings
      </button>`,
    ].join("");

    let bodyHtml = "";
    if (this._activeTab === "_hub_settings") {
      bodyHtml = this._renderHubSettingsTab(outAttr, rooms);
    } else {
      const activeSensor =
        rooms.find((r) => r.attributes.room_id === this._activeTab) || rooms[0];
      if (activeSensor) {
        bodyHtml = this._renderRoomStudioTab(activeSensor, outAttr);
      } else {
        bodyHtml = `<div class="section-card">No rooms configured yet. Open the Time-of-Day & Hub Settings tab to add an Area.</div>`;
      }
    }

    const toastHtml = this._toastMsg
      ? `<div class="toast">✓ ${escapeHtml(this._toastMsg)}</div>`
      : "";

    this.shadowRoot.innerHTML = `
      <style>${PANEL_STYLES}</style>
      <div class="panel-wrap">
        <div class="topbar">
          <div class="topbar-left">
            <button class="menu-btn" id="btn-ha-menu" title="Toggle Home Assistant Sidebar">☰</button>
            <div class="brand-title">
              <span>🔆 Perceptive Lighting Studio</span>
              <span class="brand-version">v2.0.0</span>
            </div>
          </div>
          <div class="btn-row">
            <button class="btn" id="btn-relearn-all" title="Re-scan 10 days of Recorder history for all sensors">🔄 Re-Scan Sensor History</button>
            <button class="btn danger" id="btn-reset-all-rooms" title="Reset learned thresholds and nudges for all rooms back to clean defaults">🧹 Reset All Rooms</button>
          </div>
        </div>

        <div class="outdoor-strip">
          <div>
            <div style="font-weight:700;font-size:0.95rem;">
              🌤️ Outdoor Sky: ${outLux} lx (${escapeHtml(outAttr.weather_condition || "clear")}) • Sun Elevation: ${outAttr.solar_elevation ?? "--"}°
            </div>
            <div style="font-size:0.8rem;color:var(--secondary-text-color);margin-top:2px;">
              ⏱️ <b>Time-of-Day Blend:</b> ${escapeHtml(regimeExplanation)}
            </div>
          </div>
          <div class="regime-pills">
            ${regimePillsHtml}
          </div>
        </div>

        <div class="tabs-bar">
          ${tabsHtml}
        </div>

        ${bodyHtml}
      </div>
      ${toastHtml}
    `;

    this._bindGlobalEvents();
    if (this._activeTab === "_hub_settings") {
      this._bindHubSettingsEvents(outAttr);
    } else {
      const activeSensor =
        rooms.find((r) => r.attributes.room_id === this._activeTab) || rooms[0];
      if (activeSensor) {
        this._bindRoomStudioEvents(activeSensor);
      }
    }
  }

  _renderRoomStudioTab(sensor, outAttr) {
    const attr = sensor.attributes;
    const roomId = attr.room_id;
    const roomName = attr.room_name || roomId;
    const isLocked = Boolean(attr.automation_locked);
    const isOverride = Boolean(attr.manual_override_active);
    const isOccupied = Boolean(attr.is_occupied);
    const occBy = (attr.occupied_by_entities || []).join(", ") || "None";
    const totalLights = (attr.included_lights || []).length;
    const shedCount = Number(attr.active_shed_count || 0);
    const recShedCount = Number(attr.recommended_shed_count || 0);
    const shedList = attr.shed_light_entities || [];
    const scenesMap = attr.available_scenes || {};
    const regimeWeights = attr.regime_weights || outAttr.regime_weights || {};
    const regimeConfigs = attr.regime_configs || {};

    // Build scene options dropdown HTML helper
    const buildSceneSelectOptions = (selectedVal, includeOffOption = false) => {
      const baseOpts = includeOffOption
        ? [["off", "⏹️ Turn All Lights Off (Standard Off)"]]
        : [["all_lights", "💡 All Room Lights (Dynamic Brightness)"]];

      for (const [sid, sname] of Object.entries(scenesMap)) {
        baseOpts.push([sid, sname]);
      }
      // Also include any global scene.* entities in HA if not already in scenesMap
      if (this._hass && this._hass.states) {
        Object.keys(this._hass.states)
          .filter((eid) => eid.startsWith("scene.") && !scenesMap[eid])
          .sort()
          .forEach((eid) => {
            const st = this._hass.states[eid];
            const fn = (st && st.attributes && st.attributes.friendly_name) || eid;
            baseOpts.push([eid, `HA Scene: ${fn} (${eid})`]);
          });
      }
      return baseOpts
        .map(
          ([val, label]) =>
            `<option value="${escapeHtml(val)}" ${
              val === selectedVal ? "selected" : ""
            }>${escapeHtml(label)}</option>`
        )
        .join("");
    };

    // 1. Per-Regime 4-Column Cards (Morning, Day, Evening, Overnight)
    const regimeCardsHtml = Object.entries(REGIME_META)
      .map(([rkey, meta]) => {
        const rcfg = regimeConfigs[rkey] || {};
        const rWeightPct = Math.round((Number(regimeWeights[rkey]) || 0) * 100);
        const occScene = rcfg.occupied_scene || "all_lights";
        const offScene = rcfg.off_scene || "off";
        const offWhenUnocc = rcfg.off_scene_when_unoccupied !== false;
        const offWhenBright = Boolean(rcfg.off_scene_when_bright);
        const turnOnLux = Number(rcfg.turn_on_lux ?? 10.0);
        const targetBr = Number(rcfg.target_brightness_pct ?? 60.0);
        const targetTotLux = Number(rcfg.target_total_lux ?? 12.0);
        const defShed = Number(rcfg.shed_count ?? 0);

        const lwModels = (attr.learned_weights && attr.learned_weights.regime_models) || {};
        const mSpec = lwModels[rkey] || {};
        const curIllumModel = mSpec.illum_model || "weber_fechner";
        const curWeatherModel = mSpec.weather_model || "cave_contrast";
        const curSeasonModel = mSpec.season_model || "season_neutral";

        const illumOptsHtml = Object.entries(ILLUM_MODEL_OPTIONS)
          .map(
            ([k, lbl]) =>
              `<option value="${k}" ${k === curIllumModel ? "selected" : ""}>${lbl}</option>`
          )
          .join("");
        const weatherOptsHtml = Object.entries(WEATHER_MODEL_OPTIONS)
          .map(
            ([k, lbl]) =>
              `<option value="${k}" ${k === curWeatherModel ? "selected" : ""}>${lbl}</option>`
          )
          .join("");
        const seasonOptsHtml = Object.entries(SEASON_MODEL_OPTIONS)
          .map(
            ([k, lbl]) =>
              `<option value="${k}" ${k === curSeasonModel ? "selected" : ""}>${lbl}</option>`
          )
          .join("");

        return `
          <div class="regime-card ${rWeightPct > 0 ? "active-regime" : ""}" data-regime-box="${rkey}">
            <div class="regime-card-header">
              <span>${meta.icon} ${meta.label}</span>
              <span class="regime-pill ${rWeightPct > 0 ? "active" : ""}">${rWeightPct}% Active</span>
            </div>

            <div class="field-group">
              <label>Default Occupied Scene (Scene A)</label>
              <select class="rcfg-occ-scene" data-regime="${rkey}">
                ${buildSceneSelectOptions(occScene, false)}
              </select>
            </div>

            <div class="field-group">
              <label>Off-State / Unoccupied Scene Override (Scene B)</label>
              <select class="rcfg-off-scene" data-regime="${rkey}">
                ${buildSceneSelectOptions(offScene, true)}
              </select>
              <label class="chk-row">
                <input type="checkbox" class="rcfg-off-unocc" data-regime="${rkey}" ${
                  offWhenUnocc ? "checked" : ""
                } />
                <span>Use Scene B when Room is Unoccupied</span>
              </label>
              <label class="chk-row">
                <input type="checkbox" class="rcfg-off-bright" data-regime="${rkey}" ${
                  offWhenBright ? "checked" : ""
                } />
                <span>Use Scene B when Daylight is Bright Enough</span>
              </label>
            </div>

            <div class="field-row-2">
              <div class="field-group">
                <label>Turn-On Lux (lx)</label>
                <input type="number" step="0.5" min="0" max="500" class="rcfg-turn-on-lux" data-regime="${rkey}" value="${turnOnLux}" />
              </div>
              <div class="field-group">
                <label>Target Brightness (1–100%)</label>
                <input type="number" step="1" min="1" max="100" class="rcfg-target-br" data-regime="${rkey}" value="${targetBr}" />
              </div>
            </div>

            <div class="field-row-2">
              <div class="field-group">
                <label>Target Total Room Lux (lx)</label>
                <input type="number" step="0.5" min="0.5" max="1000" class="rcfg-target-tot-lux" data-regime="${rkey}" value="${targetTotLux}" />
              </div>
              <div class="field-group">
                <label>Default Shed Lights</label>
                <input type="number" step="1" min="0" max="${Math.max(0, totalLights - 1)}" class="rcfg-shed-count" data-regime="${rkey}" value="${defShed}" />
              </div>
            </div>

            <div class="field-group">
              <label>Perceptual Illuminance Math</label>
              <select class="rcfg-illum-model" data-regime="${rkey}">${illumOptsHtml}</select>
            </div>
            <div class="field-row-2">
              <div class="field-group">
                <label>Weather Model</label>
                <select class="rcfg-weather-model" data-regime="${rkey}">${weatherOptsHtml}</select>
              </div>
              <div class="field-group">
                <label>Season / Temp Model</label>
                <select class="rcfg-season-model" data-regime="${rkey}">${seasonOptsHtml}</select>
              </div>
            </div>
          </div>
        `;
      })
      .join("");

    // 2. Per-Regime Entity Scene Triggers
    const triggers = attr.scene_triggers || [];
    const triggersListHtml = triggers
      .map((t, idx) => {
        const regLbl =
          t.regime && REGIME_META[t.regime]
            ? `${REGIME_META[t.regime].icon} ${REGIME_META[t.regime].label}`
            : "🕒 Any Time of Day";
        const statesStr = Array.isArray(t.states) ? t.states.join(", ") : t.state || "on, playing";
        const dynLbl = t.dynamic_scaling !== false ? "Dynamic Brightness Scaling" : "Fixed Scene Brightness";
        return `
          <div class="trigger-row">
            <div>
              <div style="font-weight:600;font-size:0.88rem;">
                ${regLbl} • When <code>${escapeHtml(t.entity_id)}</code> is <b>[${escapeHtml(statesStr)}]</b>
              </div>
              <div style="font-size:0.79rem;color:var(--secondary-text-color);margin-top:2px;">
                ➔ Apply Scene: <b>${escapeHtml(scenesMap[t.scene_id] || t.scene_id)}</b> (${dynLbl})
              </div>
            </div>
            <button class="btn danger btn-delete-trigger" data-idx="${idx}">🗑️ Remove</button>
          </div>
        `;
      })
      .join("");

    // Candidate trigger entities (room media players + binary sensors + all media_players/switches/input_booleans)
    const candidateTriggerEntities = [
      ...(attr.included_media_players || []),
      ...(attr.included_presence_sensors || []),
    ];
    if (this._hass && this._hass.states) {
      Object.keys(this._hass.states)
        .filter(
          (eid) =>
            (eid.startsWith("media_player.") ||
              eid.startsWith("input_boolean.") ||
              eid.startsWith("binary_sensor.") ||
              eid.startsWith("switch.")) &&
            !candidateTriggerEntities.includes(eid)
        )
        .sort()
        .forEach((eid) => candidateTriggerEntities.push(eid));
    }
    const triggerEntityOptionsHtml = candidateTriggerEntities
      .map((eid) => `<option value="${escapeHtml(eid)}">${escapeHtml(eid)}</option>`)
      .join("");

    // 3. Reorderable Light Priority List (1st to shed -> last to keep on at 1%)
    const priorityOrder = attr.light_priority_order || attr.included_lights || [];
    const lightMeta = attr.light_metadata || {};
    const shedSet = new Set(shedList);
    const lightRowsHtml = priorityOrder
      .map((eid, idx) => {
        const meta = lightMeta[eid] || {};
        const st = this._hass.states[eid];
        const isOn = st && st.state === "on";
        const brVal =
          isOn && st.attributes && st.attributes.brightness
            ? `${Math.round((Number(st.attributes.brightness) / 255) * 100)}%`
            : isOn
            ? "ON"
            : "OFF";
        const isShedNow = shedSet.has(eid);
        const positionLabel =
          idx === 0
            ? "1st to Turn Off past 1%"
            : idx === priorityOrder.length - 1
            ? "Keep On Last (Final 1% Nightlight)"
            : `#${idx + 1} to Turn Off`;

        return `
          <div class="light-order-item ${isShedNow ? "shed-now" : ""}">
            <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;">
              <span style="font-weight:700;min-width:26px;">#${idx + 1}</span>
              <div>
                <div style="font-weight:600;font-size:0.9rem;">
                  ${escapeHtml(meta.friendly_name || eid)}
                  <span class="badge-cat">${escapeHtml(meta.category_label || "Fixture")}</span>
                  ${
                    isShedNow
                      ? `<span class="badge-cat" style="background:rgba(245,158,11,0.22);color:#fbbf24;">SHED (OFF)</span>`
                      : `<span style="font-size:0.78rem;color:var(--secondary-text-color);">State: <b>${brVal}</b></span>`
                  }
                </div>
                <div style="font-size:0.76rem;color:var(--secondary-text-color);">
                  <code>${escapeHtml(eid)}</code> • ${escapeHtml(meta.model || "Light")} • <b>${positionLabel}</b>
                </div>
              </div>
            </div>
            <div class="btn-row">
              <button class="btn btn-light-up" data-idx="${idx}" ${
                idx === 0 ? "disabled style='opacity:0.4;cursor:default;'" : ""
              } title="Turn this light off earlier when dimming past 1%">⬆️ Shed Earlier</button>
              <button class="btn btn-light-down" data-idx="${idx}" ${
                idx === priorityOrder.length - 1
                  ? "disabled style='opacity:0.4;cursor:default;'"
                  : ""
              } title="Keep this light on longer when dimming past 1%">⬇️ Keep Longer</button>
            </div>
          </div>
        `;
      })
      .join("");

    // 4. Discovered Area Entities & Exclusions
    const allArea = attr.all_area_entities || {};
    const allFlat = [
      ...(allArea.illuminance_sensors || []),
      ...(allArea.presence_sensors || []),
      ...(allArea.lights || []),
      ...(allArea.media_players || []),
      ...(allArea.temperature_sensors || []),
      ...(allArea.humidity_sensors || []),
    ];
    const excludedSet = new Set(attr.excluded_entities || []);
    const autoExcludedSet = new Set(attr.auto_excluded_helpers || []);
    const entityCheckRows = allFlat
      .map((eid) => {
        const isAutoHelper = autoExcludedSet.has(eid);
        const isIncluded = !excludedSet.has(eid) && !isAutoHelper;
        const st = this._hass.states[eid];
        const curVal = st ? `${st.state} ${st.attributes.unit_of_measurement || ""}`.trim() : "N/A";
        return `
          <label class="chk-row" style="justify-content:space-between;padding:6px 10px;border-radius:8px;background:rgba(128,128,128,0.07);">
            <span>
              <b>${escapeHtml(eid)}</b>
              <span style="color:var(--secondary-text-color);font-size:0.76rem;">(${escapeHtml(curVal)})</span>
              ${
                isAutoHelper
                  ? `<span style="color:#f59e0b;font-size:0.74rem;margin-left:6px;">[Auto-Filtered Duplicate Helper]</span>`
                  : ""
              }
            </span>
            <input type="checkbox" class="chk-room-entity" data-eid="${escapeHtml(eid)}" ${
              isIncluded ? "checked" : ""
            } ${isAutoHelper ? "disabled" : ""} />
          </label>
        `;
      })
      .join("");

    return `
      <!-- SECTION 1: LIVE DIAGNOSTICS & DIRECT CONTROL -->
      <div class="section-card">
        <div class="section-title">
          <span>🔍 ${escapeHtml(roomName)} — Live Decision & Direct Control</span>
          <div class="btn-row">
            <button class="btn ${isLocked ? "danger" : "success"}" id="btn-room-lock">
              ${isLocked ? "🔒 Automation Locked (Click to Unlock)" : "✨ Auto Control Active (Click to Lock)"}
            </button>
            ${
              isOverride
                ? `<button class="btn warn" id="btn-clear-override">✋ Clear Manual Override</button>`
                : ""
            }
          </div>
        </div>

        <div class="reason-banner">
          <div><b>Why the lights are doing this right now:</b> ${escapeHtml(attr.decision_reason || "Evaluating...")}</div>
          <div style="font-size:0.8rem;color:var(--secondary-text-color);margin-top:4px;">
            Active Scene: <b>${escapeHtml(attr.resolved_scene_id || "all_lights")}</b> (${escapeHtml(
              attr.resolved_scene_reason || "Default"
            )}) • Occupied By: <b>${escapeHtml(occBy)}</b>
          </div>
          ${
            attr.ai_insight
              ? `<div style="font-size:0.8rem;color:#a5b4fc;margin-top:4px;">🧠 <b>AI Calibration Note:</b> ${escapeHtml(
                  attr.ai_insight
                )} (${attr.observation_count || 0} recorded feedback events)</div>`
              : ""
          }
        </div>

        <div class="metrics-grid">
          <div class="metric-box">
            <div class="metric-label">Total Room Lux (Measured)</div>
            <div class="metric-val">${attr.raw_lux} lx</div>
            <div class="metric-sub">Natural est: ${attr.natural_spatial_lux} lx • Target: ${attr.target_comfort_lux} lx</div>
          </div>
          <div class="metric-box">
            <div class="metric-label">Turn-On / Turn-Off Thresholds</div>
            <div class="metric-val">≤ ${attr.turn_on_lux_threshold} lx</div>
            <div class="metric-sub">Turns OFF when Total ≥ ${attr.turn_off_lux_threshold} lx</div>
          </div>
          <div class="metric-box">
            <div class="metric-label">Target Light Brightness</div>
            <div class="metric-val">${attr.recommended_brightness_pct}%</div>
            <div class="metric-sub">True 1% dimming floor • CCT: ${attr.recommended_cct_kelvin}K</div>
          </div>
          <div class="metric-box">
            <div class="metric-label">Progressive Light Shedding</div>
            <div class="metric-val">${shedCount} / ${totalLights} Shed</div>
            <div class="metric-sub">Rec shed: ${recShedCount} (${Math.max(0, totalLights - shedCount)} lights active)</div>
          </div>
        </div>

        <div class="btn-row" style="justify-content:space-between;padding-top:8px;border-top:1px solid rgba(128,128,128,0.18);">
          <div class="btn-row">
            <button class="btn primary" id="btn-direct-dimmer" title="Step brightness down (-10%) down to 1%, then turn off one light source at a time. Never turns all lights off.">
              🔉 Too Bright / Dimmer (-)
            </button>
            <button class="btn primary" id="btn-direct-brighter" title="Restore a shed light or step brightness up (+10%) and learn the resulting room lux.">
              🔆 Needs More Light / Brighter (+)
            </button>
            <button class="btn" id="btn-teach-dark" title="Teach that the room feels too dark right now: raises the turn-on lux threshold and turns lights ON immediately.">
              🌑 Feels Dark Now (Turn On)
            </button>
            <button class="btn" id="btn-teach-bright" title="Teach that the room feels bright enough right now: lowers the turn-on lux threshold and turns lights OFF.">
              ☀️ Bright Enough Now (Turn Off)
            </button>
          </div>
          <div class="btn-row">
            <button class="btn" id="btn-capture-scene">📸 Capture Current Lights as Scene</button>
            <button class="btn" id="btn-ai-calibrate">🧠 AI Calibrate Room & Lights</button>
            <button class="btn danger" id="btn-reset-room">🧹 Reset Room</button>
          </div>
        </div>
      </div>

      <!-- SECTION 2: PER-TIME-OF-DAY REGIME MATRIX -->
      <div class="section-card">
        <div class="section-title">
          <span>🕒 Per-Time-of-Day Scenes, Off-State Overrides (Scene A vs Scene B) & Thresholds</span>
          <button class="btn primary" id="btn-save-regimes">💾 Save All 4 Time-of-Day Regimes</button>
        </div>
        <div class="section-desc">
          Configure each time of day independently. You can pick a default <b>Occupied Scene (Scene A)</b>, optionally override the <b>Off / Unoccupied State with a specific Scene (Scene B)</b> instead of turning lights off, and fine-tune the Turn-On Lux Threshold and Target Brightness (1–100%). Adjacent regimes cross-fade smoothly so there are no steep drop-offs before sunset.
        </div>
        <div class="regime-grid">
          ${regimeCardsHtml}
        </div>
      </div>

      <!-- SECTION 3: PER-REGIME ENTITY SCENE TRIGGERS -->
      <div class="section-card">
        <div class="section-title">
          <span>🎬 Per-Time-of-Day Entity Scene Triggers (e.g. TV On in Day vs Evening)</span>
        </div>
        <div class="section-desc">
          Automatically switch to a specific Scene when an entity (such as a TV, Media Player, or Mode Switch) enters a given state during a specific time of day. With <b>Dynamic Brightness Scaling</b> enabled, the scene will also gently brighten and dim with real-world daylight conditions.
        </div>

        <div>
          ${
            triggersListHtml ||
            `<div style="color:var(--secondary-text-color);font-size:0.84rem;margin-bottom:12px;">No scene triggers configured for ${escapeHtml(
              roomName
            )} yet. Add one below:</div>`
          }
        </div>

        <div style="padding:12px;border-radius:12px;background:rgba(128,128,128,0.08);border:1px solid rgba(128,128,128,0.2);margin-top:10px;">
          <div style="font-weight:600;font-size:0.86rem;margin-bottom:8px;">➕ Add New Scene Trigger</div>
          <div class="regime-grid" style="grid-template-columns:repeat(auto-fit, minmax(180px, 1fr));">
            <div class="field-group">
              <label>Time of Day</label>
              <select id="new-trig-regime">
                <option value="any">🕒 Any Time of Day</option>
                <option value="morning">🌅 Morning</option>
                <option value="day">☀️ Day</option>
                <option value="evening">🌆 Evening</option>
                <option value="overnight">🌙 Overnight</option>
              </select>
            </div>
            <div class="field-group">
              <label>Trigger Entity</label>
              <select id="new-trig-entity">
                ${triggerEntityOptionsHtml}
              </select>
            </div>
            <div class="field-group">
              <label>When State Is (comma-separated)</label>
              <input type="text" id="new-trig-states" value="playing, on" />
            </div>
            <div class="field-group">
              <label>Scene to Apply</label>
              <select id="new-trig-scene">
                ${buildSceneSelectOptions("all_lights", false)}
              </select>
            </div>
          </div>
          <div class="btn-row" style="justify-content:space-between;margin-top:10px;">
            <label class="chk-row">
              <input type="checkbox" id="new-trig-dynamic" checked />
              <span>Scale Scene Brightness Dynamically with Real-World Conditions</span>
            </label>
            <button class="btn primary" id="btn-add-trigger">➕ Add Trigger</button>
          </div>
        </div>
      </div>

      <!-- SECTION 4: REORDERABLE PROGRESSIVE LIGHT SHEDDING PRIORITY -->
      <div class="section-card">
        <div class="section-title">
          <span>💡 Progressive Light Shedding Priority Order (At 1% Brightness Floor)</span>
          <div class="btn-row">
            <span style="font-size:0.78rem;color:var(--secondary-text-color);">
              ${
                attr.light_order_locked
                  ? "🔒 Custom Order Locked"
                  : "🤖 Auto-Ranked (On/Off Switches 1st ➔ Main/Spots ➔ Accent Last)"
              }
            </span>
            ${
              attr.light_order_locked
                ? `<button class="btn" id="btn-unlock-light-order">🔓 Reset to AI / Heuristic Order</button>`
                : ""
            }
          </div>
        </div>
        <div class="section-desc">
          When you press <b>Too Bright / Dimmer (-)</b> and dimmable lights have already reached <b>1%</b>, Perceptive Lighting never turns all lights off at once. Instead, each press turns off <b>one light source at a time</b> in the exact order below (#1 turns off first; the bottom light stays on at 1% until you explicitly use "Bright Enough Now / Turn Off").
        </div>
        <div class="light-order-list">
          ${lightRowsHtml || "<div>No lights discovered in this room.</div>"}
        </div>
      </div>

      <!-- SECTION 5: CONTROLLER TIMING & AREA ENTITY INCLUSION / BLACKLIST -->
      <div class="section-card">
        <div class="section-title">
          <span>⚙️ Room Controller Settings & Included Area Entities</span>
          <button class="btn primary" id="btn-save-room-general">💾 Save Room Settings & Entities</button>
        </div>
        <div class="regime-grid" style="grid-template-columns:repeat(auto-fit, minmax(190px, 1fr));margin-bottom:14px;">
          <div class="field-group">
            <label>Operating Mode</label>
            <select id="rm-op-mode">
              <option value="full_adaptive" ${attr.operating_mode === "full_adaptive" ? "selected" : ""}>Full Adaptive Room Controller</option>
              <option value="simple_transit" ${attr.operating_mode === "simple_transit" ? "selected" : ""}>Simple Transit (Hallway / Utility)</option>
              <option value="sensor_only" ${attr.operating_mode === "sensor_only" ? "selected" : ""}>Sensor-Only (No Light Control)</option>
            </select>
          </div>
          <div class="field-group">
            <label>Color Temperature (CCT) Mode</label>
            <select id="rm-cct-mode">
              <option value="external" ${attr.cct_mode === "external" ? "selected" : ""}>Brightness Only (External CCT / Flux)</option>
              <option value="perceptive_circadian" ${attr.cct_mode === "perceptive_circadian" ? "selected" : ""}>Manage Brightness + Circadian CCT</option>
            </select>
          </div>
          <div class="field-group">
            <label>Occupied On/Off Cooldown (min)</label>
            <input type="number" step="0.5" min="0.5" max="60" id="rm-cooldown-min" value="${attr.on_off_cooldown_minutes ?? 5}" />
          </div>
          <div class="field-group">
            <label>Smooth Adjust Interval (min)</label>
            <input type="number" step="0.5" min="0.5" max="30" id="rm-adjust-min" value="${attr.adjust_interval_minutes ?? 3}" />
          </div>
          <div class="field-group">
            <label>Fade Transition Duration (sec)</label>
            <input type="number" step="0.5" min="0" max="30" id="rm-transition-sec" value="${attr.transition_seconds ?? 2}" />
          </div>
        </div>

        <div style="font-weight:600;font-size:0.86rem;margin-bottom:6px;">
          📦 Area Entities (Uncheck to Exclude / Blacklist from ${escapeHtml(roomName)})
        </div>
        <div style="display:grid;grid-template-columns:repeat(auto-fit, minmax(320px, 1fr));gap:6px;">
          ${entityCheckRows}
        </div>
      </div>
    `;
  }

  _renderHubSettingsTab(outAttr, rooms) {
    const sched = outAttr.regime_schedule || {};
    const mode = sched.mode || "hybrid";
    const transMin = sched.transition_minutes ?? 45;
    const morningHr = sched.morning_start_hour ?? 6.0;
    const morningEl = sched.morning_solar_elevation ?? -4.0;
    const dayHr = sched.day_start_hour ?? 9.0;
    const dayEl = sched.day_solar_elevation ?? 8.0;
    const eveEarliestHr = sched.evening_earliest_hour ?? 16.0;
    const eveLatestHr = sched.evening_start_hour ?? 19.5;
    const eveEl = sched.evening_solar_elevation ?? 6.0;
    const ovnStartHr = sched.overnight_start_hour ?? 23.0;
    const ovnEndHr = sched.overnight_end_hour ?? 6.0;
    const gloomShift = sched.weather_gloom_shift_enabled !== false;
    const gloomLux = sched.weather_gloom_sky_lux_threshold ?? 1500.0;

    const configuredIds = new Set(rooms.map((r) => r.attributes.room_id));
    const allAreas = outAttr.all_ha_areas || [];
    const unconfiguredAreas = allAreas.filter((a) => !configuredIds.has(a.area_id));

    return `
      <div class="section-card">
        <div class="section-title">
          <span>🌍 Time-of-Day Regime Schedule, Solar Factors & Smooth Cross-Fading</span>
          <button class="btn primary" id="btn-save-hub-schedule">💾 Save Time-of-Day Schedule</button>
        </div>
        <div class="section-desc">
          Control the exact factors and times used to decide when it is <b>Morning</b>, <b>Day</b>, <b>Evening</b>, and <b>Overnight</b>.
          To prevent jarring brightness drops when you are close to sunset or a schedule boundary, Perceptive Lighting continuously blends adjacent regimes across the <b>Smooth Cross-Fade Window</b>.
        </div>

        <div class="regime-grid" style="grid-template-columns:repeat(auto-fit, minmax(260px, 1fr));margin-bottom:14px;">
          <div class="field-group">
            <label>Regime Detection Mode</label>
            <select id="hub-sched-mode">
              <option value="hybrid" ${mode === "hybrid" ? "selected" : ""}>Hybrid (Clock + Solar Elevation + Weather Gloom)</option>
              <option value="clock_only" ${mode === "clock_only" ? "selected" : ""}>Clock Schedule Only (Fixed Hours)</option>
              <option value="solar_only" ${mode === "solar_only" ? "selected" : ""}>Solar Elevation + Overnight Clock</option>
            </select>
          </div>
          <div class="field-group">
            <label>Smooth Cross-Fade Window (Minutes)</label>
            <input type="number" step="5" min="5" max="120" id="hub-sched-transition" value="${transMin}" />
          </div>
          <div class="field-group">
            <label>Weather Gloom Early-Evening Sky Threshold (lx)</label>
            <input type="number" step="100" min="100" max="10000" id="hub-sched-gloom-lux" value="${gloomLux}" />
            <label class="chk-row" style="margin-top:4px;">
              <input type="checkbox" id="hub-sched-gloom-enabled" ${gloomShift ? "checked" : ""} />
              <span>Shift toward Evening earlier on overcast/rainy afternoons</span>
            </label>
          </div>
        </div>

        <div class="regime-grid">
          <div class="regime-card">
            <div class="regime-card-header">🌅 Morning Factors</div>
            <div class="field-group">
              <label>Morning Start Hour (0–24h, e.g. 6.0 = ${formatHourDecimal(morningHr)})</label>
              <input type="number" step="0.25" min="3" max="11" id="hub-morning-hr" value="${morningHr}" />
            </div>
            <div class="field-group">
              <label>Dawn Solar Elevation Threshold (°)</label>
              <input type="number" step="0.5" min="-12" max="10" id="hub-morning-el" value="${morningEl}" />
            </div>
          </div>

          <div class="regime-card">
            <div class="regime-card-header">☀️ Day Factors</div>
            <div class="field-group">
              <label>Day Start Hour (0–24h, e.g. 9.0 = ${formatHourDecimal(dayHr)})</label>
              <input type="number" step="0.25" min="6" max="14" id="hub-day-hr" value="${dayHr}" />
            </div>
            <div class="field-group">
              <label>Daylight Solar Elevation Threshold (°)</label>
              <input type="number" step="0.5" min="0" max="25" id="hub-day-el" value="${dayEl}" />
            </div>
          </div>

          <div class="regime-card">
            <div class="regime-card-header">🌆 Evening Factors</div>
            <div class="field-group">
              <label>Earliest Evening Start Hour (${formatHourDecimal(eveEarliestHr)})</label>
              <input type="number" step="0.25" min="14" max="21" id="hub-eve-earliest-hr" value="${eveEarliestHr}" />
            </div>
            <div class="field-group">
              <label>Latest Evening Start Hour in Summer (${formatHourDecimal(eveLatestHr)})</label>
              <input type="number" step="0.25" min="16" max="23" id="hub-eve-latest-hr" value="${eveLatestHr}" />
            </div>
            <div class="field-group">
              <label>Pre-Sunset Ramp Solar Elevation (°)</label>
              <input type="number" step="0.5" min="-6" max="15" id="hub-eve-el" value="${eveEl}" />
            </div>
          </div>

          <div class="regime-card">
            <div class="regime-card-header">🌙 Overnight Factors</div>
            <div class="field-group">
              <label>Overnight Start Hour (${formatHourDecimal(ovnStartHr)})</label>
              <input type="number" step="0.25" min="20" max="24" id="hub-ovn-start-hr" value="${ovnStartHr}" />
            </div>
            <div class="field-group">
              <label>Overnight End Hour (${formatHourDecimal(ovnEndHr)})</label>
              <input type="number" step="0.25" min="3" max="10" id="hub-ovn-end-hr" value="${ovnEndHr}" />
            </div>
          </div>
        </div>
      </div>

      <div class="section-card">
        <div class="section-title">
          <span>🏠 Manage Configured Rooms (${rooms.length} Active)</span>
        </div>
        <div class="section-desc">
          Add a new Home Assistant Area to Perceptive Lighting or remove an existing room.
        </div>
        <div class="btn-row" style="margin-bottom:14px;">
          <select id="hub-add-area-select" style="max-width:300px;">
            <option value="">-- Select an Area to Add --</option>
            ${unconfiguredAreas
              .map(
                (a) =>
                  `<option value="${escapeHtml(a.area_id)}">${escapeHtml(
                    a.name
                  )} (${escapeHtml(a.area_id)})</option>`
              )
              .join("")}
          </select>
          <button class="btn primary" id="btn-hub-add-room">➕ Add Room</button>
        </div>
        <div style="display:flex;flex-wrap:wrap;gap:10px;">
          ${rooms
            .map((r) => {
              const rid = r.attributes.room_id;
              const rname = r.attributes.room_name || rid;
              return `
                <div style="padding:8px 12px;border-radius:10px;background:rgba(128,128,128,0.1);border:1px solid rgba(128,128,128,0.25);display:flex;align-items:center;gap:10px;">
                  <b>${escapeHtml(rname)}</b>
                  <button class="btn danger btn-hub-remove-room" data-room="${escapeHtml(rid)}" style="padding:4px 8px;font-size:0.74rem;">Remove</button>
                </div>
              `;
            })
            .join("")}
        </div>
      </div>
    `;
  }

  _bindGlobalEvents() {
    const menuBtn = this.shadowRoot.querySelector("#btn-ha-menu");
    if (menuBtn) {
      menuBtn.addEventListener("click", () => {
        this.dispatchEvent(
          new Event("hass-toggle-menu", { bubbles: true, composed: true })
        );
      });
    }

    this.shadowRoot.querySelectorAll(".tab-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        this._activeTab = btn.getAttribute("data-tab");
        this._render();
      });
    });

    const btnRelearn = this.shadowRoot.querySelector("#btn-relearn-all");
    if (btnRelearn) {
      btnRelearn.addEventListener("click", () => {
        this._callService(
          "relearn_history",
          {},
          "Re-scanning 10-day Recorder history for all sensors..."
        );
      });
    }

    const btnResetAll = this.shadowRoot.querySelector("#btn-reset-all-rooms");
    if (btnResetAll) {
      btnResetAll.addEventListener("click", () => {
        if (
          confirm(
            "Reset learned thresholds, nudges, and observations for ALL rooms back to clean v2.0.0 defaults?"
          )
        ) {
          this._callService(
            "reset_room",
            { room_id: "all" },
            "All rooms reset to clean defaults"
          );
        }
      });
    }
  }

  _bindRoomStudioEvents(sensor) {
    const attr = sensor.attributes;
    const roomId = attr.room_id;
    const roomName = attr.room_name || roomId;
    const isLocked = Boolean(attr.automation_locked);

    const btnLock = this.shadowRoot.querySelector("#btn-room-lock");
    if (btnLock) {
      btnLock.addEventListener("click", () => {
        this._callService(
          isLocked ? "unlock_room" : "lock_room",
          { room_id: roomId },
          isLocked ? `${roomName} unlocked` : `${roomName} automation locked`
        );
      });
    }

    const btnClearOverride = this.shadowRoot.querySelector("#btn-clear-override");
    if (btnClearOverride) {
      btnClearOverride.addEventListener("click", () => {
        this._callService(
          "apply_adaptive_scene",
          { room_id: roomId, scene: attr.active_scene_id || "default" },
          "Resumed automatic control"
        );
      });
    }

    const btnDimmer = this.shadowRoot.querySelector("#btn-direct-dimmer");
    if (btnDimmer) {
      btnDimmer.addEventListener("click", () => {
        this._callService(
          "nudge_feel",
          { room_id: roomId, direction: "too_bright", step_pct: 10 },
          "Stepped dimmer (-) & measuring resulting room lux in 5s..."
        );
      });
    }

    const btnBrighter = this.shadowRoot.querySelector("#btn-direct-brighter");
    if (btnBrighter) {
      btnBrighter.addEventListener("click", () => {
        this._callService(
          "nudge_feel",
          { room_id: roomId, direction: "more_light", step_pct: 10 },
          "Stepped brighter (+) & measuring resulting room lux in 5s..."
        );
      });
    }

    const btnTeachDark = this.shadowRoot.querySelector("#btn-teach-dark");
    if (btnTeachDark) {
      btnTeachDark.addEventListener("click", () => {
        this._callService(
          "calibrate_feels_dark_now",
          { room_id: roomId },
          "Calibrated: Feels Dark Now (Lights turning ON)"
        );
      });
    }

    const btnTeachBright = this.shadowRoot.querySelector("#btn-teach-bright");
    if (btnTeachBright) {
      btnTeachBright.addEventListener("click", () => {
        this._callService(
          "calibrate_feels_bright_now",
          { room_id: roomId },
          "Calibrated: Feels Bright Enough Now (Lights turning OFF)"
        );
      });
    }

    const btnCapture = this.shadowRoot.querySelector("#btn-capture-scene");
    if (btnCapture) {
      btnCapture.addEventListener("click", () => {
        const sname = prompt(
          `Save current ${roomName} light states as an Adaptive Scene. Enter a scene name (e.g. Evening Chill, TV Dim):`
        );
        if (sname && sname.trim()) {
          this._callService(
            "capture_adaptive_scene",
            { room_id: roomId, name: sname.trim() },
            `Saved Adaptive Scene "${sname.trim()}"`
          );
        }
      });
    }

    const btnAi = this.shadowRoot.querySelector("#btn-ai-calibrate");
    if (btnAi) {
      btnAi.addEventListener("click", () => {
        this._callService(
          "optimize_with_ai",
          { room_id: roomId },
          `Running AI Calibration & Light Priority Analysis for ${roomName}...`
        );
      });
    }

    const btnResetRoom = this.shadowRoot.querySelector("#btn-reset-room");
    if (btnResetRoom) {
      btnResetRoom.addEventListener("click", () => {
        if (
          confirm(
            `Reset all learned thresholds, nudges, and observations for ${roomName} back to clean defaults?`
          )
        ) {
          this._callService(
            "reset_room",
            { room_id: roomId },
            `${roomName} reset to clean defaults`
          );
        }
      });
    }

    // Save 4 Time-of-Day Regimes
    const btnSaveRegimes = this.shadowRoot.querySelector("#btn-save-regimes");
    if (btnSaveRegimes) {
      btnSaveRegimes.addEventListener("click", () => {
        const regimeConfigs = {};
        const regimeModels = {};
        Object.keys(REGIME_META).forEach((rkey) => {
          const occScene = this.shadowRoot.querySelector(
            `.rcfg-occ-scene[data-regime="${rkey}"]`
          )?.value;
          const offScene = this.shadowRoot.querySelector(
            `.rcfg-off-scene[data-regime="${rkey}"]`
          )?.value;
          const offUnocc = this.shadowRoot.querySelector(
            `.rcfg-off-unocc[data-regime="${rkey}"]`
          )?.checked;
          const offBright = this.shadowRoot.querySelector(
            `.rcfg-off-bright[data-regime="${rkey}"]`
          )?.checked;
          const turnOnLux = parseFloat(
            this.shadowRoot.querySelector(
              `.rcfg-turn-on-lux[data-regime="${rkey}"]`
            )?.value || "10"
          );
          const targetBr = parseFloat(
            this.shadowRoot.querySelector(
              `.rcfg-target-br[data-regime="${rkey}"]`
            )?.value || "60"
          );
          const targetTotLux = parseFloat(
            this.shadowRoot.querySelector(
              `.rcfg-target-tot-lux[data-regime="${rkey}"]`
            )?.value || "12"
          );
          const shedCnt = parseInt(
            this.shadowRoot.querySelector(
              `.rcfg-shed-count[data-regime="${rkey}"]`
            )?.value || "0",
            10
          );

          regimeConfigs[rkey] = {
            occupied_scene: occScene || "all_lights",
            off_scene: offScene || "off",
            off_scene_when_unoccupied: Boolean(offUnocc),
            off_scene_when_bright: Boolean(offBright),
            turn_on_lux: isNaN(turnOnLux) ? 10 : turnOnLux,
            target_brightness_pct: isNaN(targetBr) ? 60 : Math.max(1, Math.min(100, targetBr)),
            target_total_lux: isNaN(targetTotLux) ? 12 : targetTotLux,
            shed_count: isNaN(shedCnt) ? 0 : Math.max(0, shedCnt),
          };

          regimeModels[rkey] = {
            illum_model:
              this.shadowRoot.querySelector(
                `.rcfg-illum-model[data-regime="${rkey}"]`
              )?.value || "weber_fechner",
            weather_model:
              this.shadowRoot.querySelector(
                `.rcfg-weather-model[data-regime="${rkey}"]`
              )?.value || "cave_contrast",
            season_model:
              this.shadowRoot.querySelector(
                `.rcfg-season-model[data-regime="${rkey}"]`
              )?.value || "season_neutral",
          };
        });

        this._callService(
          "update_room_config",
          {
            room_id: roomId,
            regime_configs: regimeConfigs,
            regime_models: regimeModels,
          },
          `Saved Time-of-Day Regime settings for ${roomName}`
        );
      });
    }

    // Add / Remove Scene Triggers
    const btnAddTrigger = this.shadowRoot.querySelector("#btn-add-trigger");
    if (btnAddTrigger) {
      btnAddTrigger.addEventListener("click", () => {
        const regime =
          this.shadowRoot.querySelector("#new-trig-regime")?.value || "any";
        const entityId =
          this.shadowRoot.querySelector("#new-trig-entity")?.value || "";
        const rawStates =
          this.shadowRoot.querySelector("#new-trig-states")?.value || "on, playing";
        const sceneId =
          this.shadowRoot.querySelector("#new-trig-scene")?.value || "all_lights";
        const dynScale = Boolean(
          this.shadowRoot.querySelector("#new-trig-dynamic")?.checked
        );
        if (!entityId) return;

        const statesList = rawStates
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean);
        const nextTriggers = [...(attr.scene_triggers || [])];
        nextTriggers.push({
          id: `trig_${Date.now()}`,
          regime,
          entity_id: entityId,
          states: statesList.length > 0 ? statesList : ["on", "playing"],
          scene_id: sceneId,
          dynamic_scaling: dynScale,
        });

        this._callService(
          "update_room_config",
          { room_id: roomId, scene_triggers: nextTriggers },
          "Added scene trigger"
        );
      });
    }

    this.shadowRoot.querySelectorAll(".btn-delete-trigger").forEach((btn) => {
      btn.addEventListener("click", () => {
        const idx = parseInt(btn.getAttribute("data-idx"), 10);
        const nextTriggers = [...(attr.scene_triggers || [])];
        if (!isNaN(idx) && idx >= 0 && idx < nextTriggers.length) {
          nextTriggers.splice(idx, 1);
          this._callService(
            "update_room_config",
            { room_id: roomId, scene_triggers: nextTriggers },
            "Removed scene trigger"
          );
        }
      });
    });

    // Reorder Light Priority List
    const priorityOrder = [...(attr.light_priority_order || attr.included_lights || [])];
    this.shadowRoot.querySelectorAll(".btn-light-up").forEach((btn) => {
      btn.addEventListener("click", () => {
        const idx = parseInt(btn.getAttribute("data-idx"), 10);
        if (idx > 0 && idx < priorityOrder.length) {
          const tmp = priorityOrder[idx - 1];
          priorityOrder[idx - 1] = priorityOrder[idx];
          priorityOrder[idx] = tmp;
          this._callService(
            "update_room_config",
            {
              room_id: roomId,
              light_priority_order: priorityOrder,
              light_order_locked: true,
            },
            "Updated light shedding priority order"
          );
        }
      });
    });

    this.shadowRoot.querySelectorAll(".btn-light-down").forEach((btn) => {
      btn.addEventListener("click", () => {
        const idx = parseInt(btn.getAttribute("data-idx"), 10);
        if (idx >= 0 && idx < priorityOrder.length - 1) {
          const tmp = priorityOrder[idx + 1];
          priorityOrder[idx + 1] = priorityOrder[idx];
          priorityOrder[idx] = tmp;
          this._callService(
            "update_room_config",
            {
              room_id: roomId,
              light_priority_order: priorityOrder,
              light_order_locked: true,
            },
            "Updated light shedding priority order"
          );
        }
      });
    });

    const btnUnlockOrder = this.shadowRoot.querySelector("#btn-unlock-light-order");
    if (btnUnlockOrder) {
      btnUnlockOrder.addEventListener("click", () => {
        this._callService(
          "update_room_config",
          { room_id: roomId, light_order_locked: false, light_priority_order: [] },
          "Restored automatic fixture priority order"
        );
      });
    }

    // Save Room Controller & Exclusions
    const btnSaveGeneral = this.shadowRoot.querySelector("#btn-save-room-general");
    if (btnSaveGeneral) {
      btnSaveGeneral.addEventListener("click", () => {
        const opMode = this.shadowRoot.querySelector("#rm-op-mode")?.value;
        const cctMode = this.shadowRoot.querySelector("#rm-cct-mode")?.value;
        const cooldownMin = parseFloat(
          this.shadowRoot.querySelector("#rm-cooldown-min")?.value || "5"
        );
        const adjustMin = parseFloat(
          this.shadowRoot.querySelector("#rm-adjust-min")?.value || "3"
        );
        const transSec = parseFloat(
          this.shadowRoot.querySelector("#rm-transition-sec")?.value || "2"
        );

        const excluded = [];
        this.shadowRoot.querySelectorAll(".chk-room-entity").forEach((chk) => {
          if (!chk.disabled && !chk.checked) {
            excluded.push(chk.getAttribute("data-eid"));
          }
        });

        this._callService(
          "update_room_config",
          {
            room_id: roomId,
            operating_mode: opMode,
            cct_mode: cctMode,
            on_off_cooldown_minutes: cooldownMin,
            adjust_interval_minutes: adjustMin,
            transition_seconds: transSec,
            excluded_entities: excluded,
          },
          `Saved controller settings & entity inclusions for ${roomName}`
        );
      });
    }
  }

  _bindHubSettingsEvents() {
    const btnSaveSched = this.shadowRoot.querySelector("#btn-save-hub-schedule");
    if (btnSaveSched) {
      btnSaveSched.addEventListener("click", () => {
        const regimeSchedule = {
          mode: this.shadowRoot.querySelector("#hub-sched-mode")?.value || "hybrid",
          transition_minutes: parseFloat(
            this.shadowRoot.querySelector("#hub-sched-transition")?.value || "45"
          ),
          weather_gloom_sky_lux_threshold: parseFloat(
            this.shadowRoot.querySelector("#hub-sched-gloom-lux")?.value || "1500"
          ),
          weather_gloom_shift_enabled: Boolean(
            this.shadowRoot.querySelector("#hub-sched-gloom-enabled")?.checked
          ),
          morning_start_hour: parseFloat(
            this.shadowRoot.querySelector("#hub-morning-hr")?.value || "6"
          ),
          morning_solar_elevation: parseFloat(
            this.shadowRoot.querySelector("#hub-morning-el")?.value || "-4"
          ),
          day_start_hour: parseFloat(
            this.shadowRoot.querySelector("#hub-day-hr")?.value || "9"
          ),
          day_solar_elevation: parseFloat(
            this.shadowRoot.querySelector("#hub-day-el")?.value || "8"
          ),
          evening_earliest_hour: parseFloat(
            this.shadowRoot.querySelector("#hub-eve-earliest-hr")?.value || "16"
          ),
          evening_start_hour: parseFloat(
            this.shadowRoot.querySelector("#hub-eve-latest-hr")?.value || "19.5"
          ),
          evening_solar_elevation: parseFloat(
            this.shadowRoot.querySelector("#hub-eve-el")?.value || "6"
          ),
          overnight_start_hour: parseFloat(
            this.shadowRoot.querySelector("#hub-ovn-start-hr")?.value || "23"
          ),
          overnight_end_hour: parseFloat(
            this.shadowRoot.querySelector("#hub-ovn-end-hr")?.value || "6"
          ),
        };

        this._callService(
          "update_hub_config",
          { regime_schedule: regimeSchedule },
          "Saved global Time-of-Day Regime Schedule & Solar Factors"
        );
      });
    }

    const btnAddRoom = this.shadowRoot.querySelector("#btn-hub-add-room");
    if (btnAddRoom) {
      btnAddRoom.addEventListener("click", () => {
        const areaId = this.shadowRoot.querySelector("#hub-add-area-select")?.value;
        if (areaId) {
          this._callService(
            "update_hub_config",
            { add_area_id: areaId },
            `Added room "${areaId}"`
          );
        }
      });
    }

    this.shadowRoot.querySelectorAll(".btn-hub-remove-room").forEach((btn) => {
      btn.addEventListener("click", () => {
        const rid = btn.getAttribute("data-room");
        if (rid && confirm(`Remove room "${rid}" from Perceptive Lighting?`)) {
          this._callService(
            "update_hub_config",
            { remove_room_id: rid },
            `Removed room "${rid}"`
          );
        }
      });
    });
  }
}

/* ==========================================================================
 * 2. COMPACT LOVELACE ROOM CARD: <perceptive-room-card>
 * ========================================================================== */

const CARD_STYLES = `
  :host {
    display: block;
  }
  ha-card {
    padding: 16px;
    border-radius: var(--ha-card-border-radius, 16px);
    background: var(--ha-card-background, var(--card-background-color, #1e1e2e));
    color: var(--primary-text-color);
    box-sizing: border-box;
  }
  .header-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    margin-bottom: 10px;
  }
  .room-title {
    font-size: 1.12rem;
    font-weight: 700;
  }
  .subtitle {
    font-size: 0.8rem;
    color: var(--secondary-text-color);
    margin-top: 2px;
  }
  .pill-badge {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    padding: 4px 10px;
    border-radius: 999px;
    font-size: 0.75rem;
    font-weight: 600;
    cursor: pointer;
    border: 1px solid rgba(128, 128, 128, 0.25);
    background: rgba(128, 128, 128, 0.12);
    text-decoration: none;
    color: var(--primary-text-color);
  }
  .pill-badge.auto {
    background: rgba(16, 185, 129, 0.15);
    color: #10b981;
    border-color: rgba(16, 185, 129, 0.35);
  }
  .pill-badge.locked {
    background: rgba(239, 68, 68, 0.18);
    color: #ef4444;
    border-color: rgba(239, 68, 68, 0.4);
  }
  .reason-box {
    padding: 8px 11px;
    border-radius: 10px;
    background: rgba(59, 130, 246, 0.1);
    border: 1px solid rgba(59, 130, 246, 0.28);
    font-size: 0.78rem;
    line-height: 1.4;
    margin: 8px 0;
  }
  .scenes-grid {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin: 10px 0;
  }
  .scene-chip {
    flex: 1 1 auto;
    min-width: 82px;
    text-align: center;
    padding: 7px 10px;
    border-radius: 10px;
    font-size: 0.79rem;
    font-weight: 500;
    cursor: pointer;
    border: 1px solid rgba(128, 128, 128, 0.24);
    background: rgba(128, 128, 128, 0.09);
  }
  .scene-chip.active {
    background: var(--primary-color, #3b82f6);
    color: #fff;
    border-color: var(--primary-color, #3b82f6);
    font-weight: 600;
  }
  .tune-row {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin-top: 10px;
    padding-top: 10px;
    border-top: 1px solid rgba(128, 128, 128, 0.16);
  }
  .btn-action {
    padding: 6px 10px;
    border-radius: 9px;
    font-size: 0.77rem;
    font-weight: 600;
    cursor: pointer;
    border: 1px solid rgba(128, 128, 128, 0.26);
    background: rgba(128, 128, 128, 0.1);
    color: var(--primary-text-color);
  }
`;

class PerceptiveRoomCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = {};
    this._hass = null;
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
    if (!config) throw new Error("Invalid configuration");
    this._config = config;
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  _callService(service, data) {
    if (this._hass) this._hass.callService(DOMAIN, service, data);
  }

  _render() {
    if (!this.shadowRoot) return;
    const sensor = findRoomSensor(this._hass, this._config.room);
    if (!sensor) {
      this.shadowRoot.innerHTML = `
        <style>${CARD_STYLES}</style>
        <ha-card>
          <div class="room-title">🔆 Perceptive Lighting Room Card</div>
          <div class="subtitle">Select a configured room in the card editor.</div>
        </ha-card>
      `;
      return;
    }

    const attr = sensor.attributes;
    const roomId = attr.room_id;
    const roomName = this._config.title || attr.room_name || roomId;
    const isLocked = Boolean(attr.automation_locked);
    const isOccupied = Boolean(attr.is_occupied);
    const scenesMap = attr.available_scenes || {};
    const activeSceneId = attr.active_scene_id || "default";
    const activeActivity = attr.active_activity || "relaxing";
    const totalLights = (attr.included_lights || []).length;
    const shedCount = Number(attr.active_shed_count || 0);

    const sceneChipsHtml = Object.entries(scenesMap)
      .map(([sid, label]) => {
        const isActive =
          sid === activeSceneId ||
          (activeSceneId === "default" && sid === `activity_${activeActivity}`);
        const cleanLabel = String(label).replace(/^Scene:\s*/i, "");
        return `<div class="scene-chip ${isActive ? "active" : ""}" data-scene="${escapeHtml(
          sid
        )}">${escapeHtml(cleanLabel)}</div>`;
      })
      .join("");

    this.shadowRoot.innerHTML = `
      <style>${CARD_STYLES}</style>
      <ha-card>
        <div class="header-row">
          <div>
            <div class="room-title">${escapeHtml(roomName)}</div>
            <div class="subtitle">
              ${escapeHtml((attr.time_regime || "day").toUpperCase())} • Measured: <b>${attr.raw_lux} lx</b> (Turn-On ≤ ${attr.turn_on_lux_threshold} lx) • Target: <b>${attr.recommended_brightness_pct}%</b> (${shedCount}/${totalLights} shed)
            </div>
          </div>
          <div style="display:flex;gap:6px;align-items:center;">
            <div class="pill-badge ${isLocked ? "locked" : "auto"}" id="btn-toggle-lock">
              ${isLocked ? "🔒 Locked" : `✨ Auto (${isOccupied ? "Occupied" : "Vacant"})`}
            </div>
            <a class="pill-badge" href="/perceptive-lighting" title="Open Full Perceptive Lighting Sidebar Studio">⚙️ Studio</a>
          </div>
        </div>

        <div class="reason-box">
          ${escapeHtml(attr.decision_reason || "Evaluating room lighting...")}
        </div>

        <div class="scenes-grid">
          ${sceneChipsHtml}
        </div>

        <div class="tune-row">
          <button class="btn-action" id="btn-nudge-darker" title="Step dimmer down to 1%, then turn off one light at a time">🔉 Too Bright (-)</button>
          <button class="btn-action" id="btn-nudge-lighter" title="Restore shed light or step brightness up">🔆 Brighter (+)</button>
          <button class="btn-action" id="btn-feels-dark-now" title="Turn lights ON and raise Turn-On Lux Threshold">🌑 Feels Dark Now</button>
          <button class="btn-action" id="btn-feels-bright-now" title="Turn lights OFF and lower Turn-On Lux Threshold">☀️ Bright Enough</button>
          <button class="btn-action" id="btn-reset-room" title="Reset learned thresholds for this room">🧹 Reset</button>
        </div>
      </ha-card>
    `;

    this.shadowRoot
      .querySelector("#btn-toggle-lock")
      ?.addEventListener("click", () => {
        this._callService(isLocked ? "unlock_room" : "lock_room", {
          room_id: roomId,
        });
      });

    this.shadowRoot.querySelectorAll(".scene-chip").forEach((chip) => {
      chip.addEventListener("click", () => {
        this._callService("apply_adaptive_scene", {
          room_id: roomId,
          scene: chip.getAttribute("data-scene"),
        });
      });
    });

    this.shadowRoot
      .querySelector("#btn-nudge-darker")
      ?.addEventListener("click", () => {
        this._callService("nudge_feel", {
          room_id: roomId,
          direction: "too_bright",
          step_pct: 10,
        });
      });

    this.shadowRoot
      .querySelector("#btn-nudge-lighter")
      ?.addEventListener("click", () => {
        this._callService("nudge_feel", {
          room_id: roomId,
          direction: "more_light",
          step_pct: 10,
        });
      });

    this.shadowRoot
      .querySelector("#btn-feels-dark-now")
      ?.addEventListener("click", () => {
        this._callService("calibrate_feels_dark_now", { room_id: roomId });
      });

    this.shadowRoot
      .querySelector("#btn-feels-bright-now")
      ?.addEventListener("click", () => {
        this._callService("calibrate_feels_bright_now", { room_id: roomId });
      });

    this.shadowRoot
      .querySelector("#btn-reset-room")
      ?.addEventListener("click", () => {
        if (confirm(`Reset calibration for ${roomName}?`)) {
          this._callService("reset_room", { room_id: roomId });
        }
      });
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
        return `<option value="${escapeHtml(rid)}" ${selected}>${escapeHtml(
          rname
        )}</option>`;
      })
      .join("");

    this.shadowRoot.innerHTML = `
      <div style="display:flex;flex-direction:column;gap:10px;padding:8px 0;">
        <label style="display:flex;flex-direction:column;gap:4px;">
          <span>Select Perceptive Lighting Room</span>
          <select id="select-room" style="padding:8px;border-radius:8px;">
            <option value="">-- Automatic / First Room --</option>
            ${optionsHtml}
          </select>
        </label>
      </div>
    `;

    this.shadowRoot
      .querySelector("#select-room")
      ?.addEventListener("change", (ev) => {
        this._config = { ...this._config, room: ev.target.value };
        this.dispatchEvent(
          new CustomEvent("config-changed", {
            detail: { config: this._config },
            bubbles: true,
            composed: true,
          })
        );
      });
  }
}

/* ==========================================================================
 * 3. WHOLE-HOME OVERVIEW CARD: <perceptive-home-card>
 * ========================================================================== */

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

    const roomRowsHtml = rooms
      .map((st) => {
        const a = st.attributes;
        const rid = a.room_id;
        const rname = a.room_name || rid;
        const isLocked = Boolean(a.automation_locked);
        const isOcc = Boolean(a.is_occupied);
        return `
          <div style="padding:10px 12px;border-radius:12px;background:rgba(128,128,128,0.09);margin-bottom:8px;display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap;">
            <div>
              <div style="font-weight:600;font-size:0.95rem;">
                ${escapeHtml(rname)}
                <span style="font-size:0.76rem;font-weight:400;color:var(--secondary-text-color);">
                  (${isOcc ? "Occupied" : "Vacant"} • ${a.raw_lux} lx / Turn-On ≤ ${a.turn_on_lux_threshold} lx)
                </span>
              </div>
              <div style="font-size:0.78rem;color:var(--secondary-text-color);margin-top:2px;">
                ${escapeHtml(a.decision_reason || "")}
              </div>
            </div>
            <div class="pill-badge ${isLocked ? "locked" : "auto"}" data-lock-room="${escapeHtml(
              rid
            )}" data-locked="${isLocked}">
              ${isLocked ? "🔒 Locked" : "✨ Auto"}
            </div>
          </div>
        `;
      })
      .join("");

    this.shadowRoot.innerHTML = `
      <style>${CARD_STYLES}</style>
      <ha-card>
        <div class="header-row">
          <div>
            <div class="room-title">${escapeHtml(
              this._config.title || "🌤️ Perceptive Lighting — Home"
            )}</div>
            <div class="subtitle">
              Outdoor Sky: <b>${outLux} lx</b> • ${escapeHtml(
                outAttr.regime_explanation || ""
              )}
            </div>
          </div>
          <a class="pill-badge" href="/perceptive-lighting">⚙️ Open Studio</a>
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
  }

  getCardSize() {
    return 4;
  }
}

if (!customElements.get("perceptive-lighting-panel")) {
  customElements.define("perceptive-lighting-panel", PerceptiveLightingPanel);
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
      "Live decision reason, Direct Brightness & Progressive Light Shedding (+ / -), Scenes, and Calibration.",
    preview: true,
  });
}
if (!window.customCards.some((c) => c.type === "perceptive-home-card")) {
  window.customCards.push({
    type: "perceptive-home-card",
    name: "Perceptive Lighting: Whole-Home Card",
    description:
      "Whole-home Perceptive Lighting overview, Time-of-Day blend, and quick link to the Sidebar Studio.",
    preview: true,
  });
}
