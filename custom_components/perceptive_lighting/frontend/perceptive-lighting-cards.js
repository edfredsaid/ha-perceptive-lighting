/**
 * Perceptive Lighting v2.1.0 — Layered Consumer HCI Studio & Lovelace Cards
 *
 * Architecture:
 * - Layer 1 (Calm Room Workspace):
 *   1. Live Room Status & Tactile Light Control (Dimmer - / Brighter +, Active Scene,
 *      1-tap Resume Auto / Lock, and 1-tap "Should Be On Now" / "Bright Enough").
 *   2. Auto-Saving 4-Row Daily Schedule Matrix (Morning, Day, Evening, Overnight —
 *      Occupied Scene, Target Brightness 1-100%, Turn-On Lux, and Vacant/Off Scene).
 *   3. Human-Readable Conditional Scene Rules (with live "ACTIVE NOW" badge).
 * - Layer 2 (Task-Driven Slide-Over Drawers & Modals):
 *   - Drawer "lights": Progressive 1% Light Shedding Priority Order
 *   - Drawer "sensors": Occupancy Hold Timer, Presence/FP2 Toggles, Lux Sensor Roles, & Entity Inclusion
 *   - Drawer "tuning": Operating Mode, Kruithof Circadian CCT, Math Models, AI & Room Reset
 *   - Drawer "hub": Global Time-of-Day Clock/Solar Schedule & Room Manager
 *   - Modal "add_rule": Step-by-step Conditional Rule Builder
 *   - Modal "capture_scene": Inline Scene Capture Dialog (no window.prompt)
 *   - Modal "off_scene": Per-Regime Vacant / Off-State Scene Override Configurator
 */

const DOMAIN = "perceptive_lighting";

const REGIME_META = {
  morning: { label: "Morning", icon: "🌅", short: "Morning" },
  day: { label: "Day", icon: "☀️", short: "Day" },
  evening: { label: "Evening", icon: "🌆", short: "Evening" },
  overnight: { label: "Overnight", icon: "🌙", short: "Overnight" },
};

// Exact backend constant keys matching const.py & engine.py
const ILLUM_MODEL_OPTIONS = {
  weber_fechner: "Weber-Fechner Logarithmic (Recommended)",
  stevens_power: "Stevens' Power Law",
  naka_rushton: "Naka-Rushton Retinal Sigmoid",
  cuttle_mrse: "Cuttle Ambient MRSE",
  cie_daylight_factor: "CIE Daylight Factor",
};

const WEATHER_MODEL_OPTIONS = {
  cave_contrast: "Window Contrast (Cave Effect)",
  gloom_compensation: "Overcast Gloom Uplift",
  solar_altitude: "Solar Altitude Penetration",
  weather_neutral: "Weather Neutral (Indoor Lux Only)",
};

const SEASON_MODEL_OPTIONS = {
  combined_bioclimatic: "Bioclimatic (Season + Temp)",
  photoperiod_sad: "Winter Photoperiod Boost",
  thermal_hue_heat: "Thermal Hue-Heat Balance",
  season_neutral: "Season Neutral (1.0x)",
};

const SENSOR_PLACEMENT_OPTIONS = {
  auto_learn: "Balanced / True Lux (1.0x)",
  balanced: "Mid-Room Balanced (1.0x)",
  near_window: "Windowsill / Dampen Glare (0.65x)",
  dim_corner: "Shaded Corner / Boost (1.35x)",
  ceiling: "Ceiling Downward (1.10x)",
};

const VACANCY_DELAY_PRESETS = [
  { value: 0, label: "Immediate (0s — No Hold)" },
  { value: 15, label: "15 seconds" },
  { value: 30, label: "30 seconds (Transit / Hall)" },
  { value: 60, label: "1 minute" },
  { value: 90, label: "1.5 minutes (Recommended)" },
  { value: 120, label: "2 minutes" },
  { value: 180, label: "3 minutes" },
  { value: 300, label: "5 minutes (Dwelling / Office)" },
  { value: 600, label: "10 minutes" },
];

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

function findAllRoomSensors(hass) {
  if (!hass || !hass.states) return [];
  const hubOut = findHubOutdoorSensor(hass);
  const configuredSet =
    hubOut && Array.isArray(hubOut.attributes.configured_rooms)
      ? new Set(hubOut.attributes.configured_rooms)
      : null;
  return Object.values(hass.states)
    .filter(
      (st) =>
        st.entity_id.startsWith("sensor.") &&
        st.state !== "unavailable" &&
        st.attributes &&
        st.attributes.room_id &&
        st.attributes.available_scenes !== undefined &&
        (!configuredSet || configuredSet.has(st.attributes.room_id))
    )
    .sort((a, b) =>
      String(a.attributes.room_name || a.attributes.room_id).localeCompare(
        String(b.attributes.room_name || b.attributes.room_id)
      )
    );
}

function findRoomSensor(hass, roomKey) {
  const rooms = findAllRoomSensors(hass);
  if (!roomKey) return rooms[0] || null;
  const keyLow = String(roomKey).toLowerCase();
  return (
    rooms.find(
      (st) =>
        st.attributes.room_id === roomKey ||
        st.attributes.area_id === roomKey ||
        st.entity_id === roomKey ||
        String(st.attributes.room_name || "").toLowerCase() === keyLow
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

function decimalHourToTimeStr(h) {
  const num = Math.max(0, Math.min(23.99, Number(h) || 0));
  const hrs = Math.floor(num);
  const mins = Math.round((num - hrs) * 60) % 60;
  return `${String(hrs).padStart(2, "0")}:${String(mins).padStart(2, "0")}`;
}

function timeStrToDecimalHour(str, fallback = 12.0) {
  if (!str || typeof str !== "string" || !str.includes(":")) return fallback;
  const [hStr, mStr] = str.split(":");
  const h = parseInt(hStr, 10);
  const m = parseInt(mStr, 10) || 0;
  if (isNaN(h)) return fallback;
  return Math.round((h + m / 60.0) * 100) / 100;
}

function getFriendlyEntityName(hass, attr, eid) {
  if (!eid) return "";
  if (attr && attr.entity_friendly_names && attr.entity_friendly_names[eid]) {
    return attr.entity_friendly_names[eid];
  }
  if (attr && attr.light_metadata && attr.light_metadata[eid]?.friendly_name) {
    return attr.light_metadata[eid].friendly_name;
  }
  const st = hass && hass.states ? hass.states[eid] : null;
  if (st && st.attributes && st.attributes.friendly_name) {
    return st.attributes.friendly_name;
  }
  return eid
    .replace(/^[a-z_]+\./, "")
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

/* ==========================================================================
 * 1. LAYERED CONSUMER SIDEBAR STUDIO: <perceptive-lighting-panel>
 * ========================================================================== */

const PANEL_STYLES = `
  :host {
    display: block;
    min-height: 100vh;
    background: var(--primary-background-color, #0b0f17);
    color: var(--primary-text-color, #f8fafc);
    font-family: var(--paper-font-body1_-_font-family, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif);
    box-sizing: border-box;
    -webkit-font-smoothing: antialiased;
  }
  .app-shell {
    max-width: 920px;
    margin: 0 auto;
    padding: 18px 16px 72px 16px;
  }
  /* Header */
  .top-bar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    margin-bottom: 16px;
    flex-wrap: wrap;
  }
  .brand-row {
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .icon-btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 38px;
    height: 38px;
    border-radius: 10px;
    border: 1px solid rgba(148, 163, 184, 0.2);
    background: var(--ha-card-background, var(--card-background-color, #161f30));
    color: var(--primary-text-color);
    cursor: pointer;
    font-size: 1.05rem;
  }
  .brand-title {
    font-size: 1.2rem;
    font-weight: 700;
    letter-spacing: -0.02em;
  }
  .top-actions {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }
  .ambient-badge {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 6px 12px;
    border-radius: 999px;
    font-size: 0.78rem;
    font-weight: 600;
    background: rgba(59, 130, 246, 0.12);
    color: #93c5fd;
    border: 1px solid rgba(59, 130, 246, 0.25);
  }
  /* Room Switcher Bar */
  .room-bar {
    display: flex;
    gap: 8px;
    overflow-x: auto;
    padding-bottom: 6px;
    margin-bottom: 18px;
    scrollbar-width: none;
  }
  .room-bar::-webkit-scrollbar {
    display: none;
  }
  .room-tab {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    padding: 9px 16px;
    border-radius: 999px;
    font-size: 0.86rem;
    font-weight: 600;
    white-space: nowrap;
    cursor: pointer;
    border: 1px solid rgba(148, 163, 184, 0.18);
    background: var(--ha-card-background, var(--card-background-color, #161f30));
    color: var(--secondary-text-color, #94a3b8);
    transition: all 0.15s ease;
  }
  .room-tab.active {
    background: var(--primary-color, #3b82f6);
    color: #ffffff;
    border-color: var(--primary-color, #3b82f6);
    box-shadow: 0 4px 12px rgba(59, 130, 246, 0.28);
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
  .room-tab.active .dot.occ {
    background: #a7f3d0;
  }
  .dot.hold {
    background: #f59e0b;
  }
  .dot.vac {
    background: rgba(148, 163, 184, 0.45);
  }
  /* Cards */
  .card {
    background: var(--ha-card-background, var(--card-background-color, #161f30));
    border: 1px solid rgba(148, 163, 184, 0.14);
    border-radius: 20px;
    padding: 20px;
    margin-bottom: 16px;
    box-shadow: 0 4px 20px rgba(0, 0, 0, 0.14);
  }
  .card-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    margin-bottom: 12px;
    flex-wrap: wrap;
  }
  .card-title {
    font-size: 1.02rem;
    font-weight: 700;
    letter-spacing: -0.01em;
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .card-desc {
    font-size: 0.81rem;
    color: var(--secondary-text-color, #94a3b8);
    line-height: 1.45;
    margin-bottom: 14px;
  }
  /* Hero Live Room Card */
  .hero-header {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 12px;
    flex-wrap: wrap;
  }
  .room-heading {
    font-size: 1.4rem;
    font-weight: 800;
    letter-spacing: -0.02em;
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
  }
  .status-pill {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px 11px;
    border-radius: 999px;
    font-size: 0.75rem;
    font-weight: 700;
  }
  .status-pill.occ {
    background: rgba(16, 185, 129, 0.15);
    color: #34d399;
    border: 1px solid rgba(16, 185, 129, 0.3);
  }
  .status-pill.hold {
    background: rgba(245, 158, 11, 0.16);
    color: #fbbf24;
    border: 1px solid rgba(245, 158, 11, 0.35);
  }
  .status-pill.locked {
    background: rgba(239, 68, 68, 0.16);
    color: #f87171;
    border: 1px solid rgba(239, 68, 68, 0.35);
  }
  .status-pill.vac {
    background: rgba(148, 163, 184, 0.12);
    color: var(--secondary-text-color, #94a3b8);
    border: 1px solid rgba(148, 163, 184, 0.2);
  }
  .live-sentence {
    margin-top: 8px;
    font-size: 0.86rem;
    color: var(--primary-text-color);
    opacity: 0.92;
    line-height: 1.45;
  }
  .telemetry-strip {
    display: flex;
    gap: 16px;
    flex-wrap: wrap;
    margin-top: 10px;
    padding-top: 10px;
    border-top: 1px solid rgba(148, 163, 184, 0.12);
    font-size: 0.79rem;
    color: var(--secondary-text-color, #94a3b8);
  }
  .telemetry-strip b {
    color: var(--primary-text-color);
  }
  .control-bar {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    gap: 12px;
    margin-top: 16px;
  }
  .control-box {
    background: rgba(148, 163, 184, 0.06);
    border: 1px solid rgba(148, 163, 184, 0.14);
    border-radius: 14px;
    padding: 12px 14px;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    gap: 8px;
  }
  .box-label {
    font-size: 0.74rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--secondary-text-color, #94a3b8);
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  .stepper-row {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 8px;
  }
  /* Buttons & Inputs */
  .btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 6px;
    padding: 9px 14px;
    border-radius: 11px;
    font-size: 0.83rem;
    font-weight: 600;
    cursor: pointer;
    border: 1px solid rgba(148, 163, 184, 0.22);
    background: rgba(148, 163, 184, 0.09);
    color: var(--primary-text-color);
    transition: all 0.14s ease;
  }
  .btn:hover {
    background: rgba(148, 163, 184, 0.18);
  }
  .btn:active {
    transform: scale(0.98);
  }
  .btn.primary {
    background: var(--primary-color, #3b82f6);
    color: #fff;
    border-color: var(--primary-color, #3b82f6);
  }
  .btn.amber {
    background: rgba(245, 158, 11, 0.16);
    color: #fbbf24;
    border-color: rgba(245, 158, 11, 0.35);
  }
  .btn.danger {
    background: rgba(239, 68, 68, 0.14);
    color: #f87171;
    border-color: rgba(239, 68, 68, 0.32);
  }
  .btn.sm {
    padding: 6px 11px;
    font-size: 0.77rem;
    border-radius: 9px;
  }
  select,
  input[type="text"],
  input[type="number"],
  input[type="time"] {
    width: 100%;
    box-sizing: border-box;
    padding: 8px 11px;
    border-radius: 10px;
    border: 1px solid rgba(148, 163, 184, 0.24);
    background: rgba(15, 23, 42, 0.65);
    color: var(--primary-text-color, #f8fafc);
    font-size: 0.84rem;
  }
  input[type="range"] {
    width: 100%;
    accent-color: var(--primary-color, #3b82f6);
    cursor: pointer;
  }
  /* 4-Row Daily Lighting Schedule Matrix */
  .schedule-matrix {
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  .schedule-row {
    display: grid;
    grid-template-columns: 140px 1.2fr 1.1fr 115px auto;
    align-items: center;
    gap: 12px;
    padding: 12px 14px;
    border-radius: 14px;
    background: rgba(148, 163, 184, 0.05);
    border: 1px solid rgba(148, 163, 184, 0.14);
    transition: all 0.15s ease;
  }
  .schedule-row.is-active {
    background: rgba(59, 130, 246, 0.09);
    border-color: rgba(59, 130, 246, 0.4);
  }
  @media (max-width: 720px) {
    .schedule-row {
      grid-template-columns: 1fr 1fr;
    }
  }
  .regime-name {
    font-weight: 700;
    font-size: 0.9rem;
    display: flex;
    flex-direction: column;
    gap: 2px;
  }
  .regime-active-tag {
    font-size: 0.7rem;
    font-weight: 700;
    color: #60a5fa;
  }
  .col-label {
    font-size: 0.71rem;
    color: var(--secondary-text-color, #94a3b8);
    margin-bottom: 4px;
    display: flex;
    justify-content: space-between;
  }
  /* Rule Row */
  .rule-card {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    padding: 12px 14px;
    border-radius: 13px;
    background: rgba(148, 163, 184, 0.06);
    border: 1px solid rgba(148, 163, 184, 0.15);
    margin-bottom: 8px;
  }
  .rule-card.active-rule {
    border-color: rgba(16, 185, 129, 0.45);
    background: rgba(16, 185, 129, 0.09);
  }
  /* Setup Drawer Launcher Tiles */
  .launcher-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    gap: 12px;
    margin-top: 8px;
  }
  .launcher-tile {
    text-align: left;
    padding: 15px 16px;
    border-radius: 16px;
    background: var(--ha-card-background, var(--card-background-color, #161f30));
    border: 1px solid rgba(148, 163, 184, 0.16);
    color: var(--primary-text-color);
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    transition: all 0.15s ease;
  }
  .launcher-tile:hover {
    border-color: rgba(59, 130, 246, 0.45);
    transform: translateY(-1px);
  }
  .launcher-title {
    font-size: 0.9rem;
    font-weight: 700;
    margin-bottom: 3px;
  }
  .launcher-sub {
    font-size: 0.76rem;
    color: var(--secondary-text-color, #94a3b8);
  }
  /* Layer 2 Slide-Over Drawer & Modal Overlay */
  .backdrop {
    position: fixed;
    inset: 0;
    background: rgba(3, 7, 18, 0.68);
    backdrop-filter: blur(4px);
    z-index: 1000;
    display: flex;
    justify-content: flex-end;
  }
  .backdrop.center-modal {
    justify-content: center;
    align-items: center;
    padding: 16px;
  }
  .drawer {
    width: 100%;
    max-width: 560px;
    height: 100vh;
    overflow-y: auto;
    background: var(--ha-card-background, var(--card-background-color, #141c2b));
    border-left: 1px solid rgba(148, 163, 184, 0.2);
    padding: 22px 20px 64px 20px;
    box-sizing: border-box;
    box-shadow: -12px 0 36px rgba(0, 0, 0, 0.45);
  }
  .modal-box {
    width: 100%;
    max-width: 480px;
    background: var(--ha-card-background, var(--card-background-color, #141c2b));
    border: 1px solid rgba(148, 163, 184, 0.24);
    border-radius: 20px;
    padding: 22px;
    box-sizing: border-box;
    box-shadow: 0 16px 40px rgba(0, 0, 0, 0.5);
  }
  .drawer-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    padding-bottom: 14px;
    margin-bottom: 16px;
    border-bottom: 1px solid rgba(148, 163, 184, 0.15);
  }
  .drawer-title {
    font-size: 1.12rem;
    font-weight: 800;
  }
  .section-label {
    font-size: 0.78rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: #60a5fa;
    margin: 18px 0 8px 0;
  }
  .list-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    padding: 11px 13px;
    border-radius: 12px;
    background: rgba(148, 163, 184, 0.06);
    border: 1px solid rgba(148, 163, 184, 0.14);
    margin-bottom: 8px;
  }
  .list-row.is-last-light {
    border-color: rgba(99, 102, 241, 0.45);
    background: rgba(99, 102, 241, 0.1);
  }
  .rank-badge {
    width: 28px;
    height: 28px;
    border-radius: 8px;
    background: rgba(59, 130, 246, 0.16);
    color: #60a5fa;
    font-weight: 700;
    font-size: 0.78rem;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
  }
  .toast {
    position: fixed;
    bottom: 22px;
    right: 22px;
    background: #10b981;
    color: #042f2e;
    font-weight: 700;
    font-size: 0.83rem;
    padding: 10px 16px;
    border-radius: 12px;
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.35);
    z-index: 2000;
  }
`;

class PerceptiveLightingPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._activeTab = null; // room_id
    this._activeDrawer = null; // null | "lights" | "sensors" | "tuning" | "hub"
    this._activeModal = null; // null | "add_rule" | "capture_scene" | { type: "off_scene", regime: "evening" }
    this._newRuleTriggerType = "entity";
    this._optimisticPatches = {};
    this._patchTimers = {};
    this._lastStateSignature = "";
    this._toastMsg = null;
    this._toastTimer = null;
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

    // Never clobber an open text/number/time/range/select input while the user is interacting with it
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
    if (!this._activeTab && rooms.length > 0) {
      this._activeTab = rooms[0].attributes.room_id;
    }
    const outAttr = out ? out.attributes : {};
    const parts = [
      this._activeTab,
      this._activeDrawer,
      JSON.stringify(this._activeModal),
      out
        ? `${out.state}:${JSON.stringify(outAttr.regime_weights)}:${JSON.stringify(outAttr.regime_schedule)}:${JSON.stringify(outAttr.configured_rooms)}`
        : "no_out",
    ];
    for (const r of rooms) {
      const a = r.attributes;
      parts.push(
        [
          a.room_id,
          r.state,
          a.raw_lux,
          a.effective_spatial_lux,
          a.is_occupied,
          a.vacancy_hold_active,
          a.vacancy_delay_seconds,
          a.automation_locked,
          a.manual_override_active,
          a.decision_reason,
          a.recommended_brightness_pct,
          a.active_shed_count,
          a.active_trigger_id,
          a.light_order_locked,
          JSON.stringify(a.light_priority_order),
          JSON.stringify(a.regime_configs),
          JSON.stringify(a.scene_triggers),
          JSON.stringify(a.excluded_entities),
          JSON.stringify(a.sensor_profiles),
        ].join(":")
      );
    }
    return parts.join("|");
  }

  _getEffectiveRoomAttr(sensor) {
    const base = sensor.attributes || {};
    const patch = this._optimisticPatches[base.room_id] || {};
    return { ...base, ...patch };
  }

  _applyOptimisticPatch(roomId, patch) {
    this._optimisticPatches[roomId] = {
      ...(this._optimisticPatches[roomId] || {}),
      ...patch,
    };
    if (this._patchTimers[roomId]) {
      clearTimeout(this._patchTimers[roomId]);
    }
    this._patchTimers[roomId] = setTimeout(() => {
      delete this._optimisticPatches[roomId];
    }, 5000);
    this._render();
  }

  _showToast(msg) {
    this._toastMsg = msg;
    if (this._toastTimer) clearTimeout(this._toastTimer);
    this._render();
    this._toastTimer = setTimeout(() => {
      this._toastMsg = null;
      this._render();
    }, 2400);
  }

  _callService(service, data, toastText) {
    if (!this._hass) return;
    this._hass.callService(DOMAIN, service, data);
    if (toastText) {
      this._showToast(toastText);
    }
  }

  _buildSceneOptionsHtml(scenesMap, selectedVal, includeOffOption = false) {
    const seenValues = new Set();
    const seenHaEntities = new Set();
    const baseOpts = [];

    const normalizedSelected =
      selectedVal && String(selectedVal).startsWith("scene.")
        ? `ha_${String(selectedVal).replace("scene.", "")}`
        : selectedVal;

    if (includeOffOption) {
      baseOpts.push(["off", "⏹️ Turn All Lights Off"]);
      seenValues.add("off");
    } else {
      baseOpts.push(["all_lights", "💡 All Room Lights (Dynamic)"]);
      seenValues.add("all_lights");
    }

    for (const [sid, sname] of Object.entries(scenesMap || {})) {
      if (sid !== "default" && !seenValues.has(sid)) {
        baseOpts.push([sid, sname]);
        seenValues.add(sid);
        if (sid.startsWith("ha_")) {
          seenHaEntities.add(`scene.${sid.slice(3)}`);
        }
      }
    }

    return baseOpts
      .map(
        ([val, label]) =>
          `<option value="${escapeHtml(val)}" ${
            val === normalizedSelected || val === selectedVal ? "selected" : ""
          }>${escapeHtml(label)}</option>`
      )
      .join("");
  }

  _render() {
    if (!this.shadowRoot || !this._hass) return;

    const drawerEl = this.shadowRoot.querySelector(".drawer");
    const prevDrawerScroll = drawerEl ? drawerEl.scrollTop : 0;

    const outdoor = findHubOutdoorSensor(this._hass);
    const rooms = findAllRoomSensors(this._hass);
    if (!this._activeTab && rooms.length > 0) {
      this._activeTab = rooms[0].attributes.room_id;
    }

    const outAttr = outdoor ? outdoor.attributes : {};
    const outLux = outdoor ? Math.round(Number(outdoor.state) || 0) : 0;
    const curOutdoorRegime = outAttr.time_regime || "evening";
    const activeRegimeMeta = REGIME_META[curOutdoorRegime] || REGIME_META.day;

    const activeSensor =
      rooms.find((r) => r.attributes.room_id === this._activeTab) || rooms[0];

    const roomTabsHtml = rooms
      .map((st) => {
        const a = this._getEffectiveRoomAttr(st);
        const rid = a.room_id;
        const rname = a.room_name || rid;
        const isAct = activeSensor && activeSensor.attributes.room_id === rid;
        const dotClass = a.manual_override_active || a.automation_locked
          ? "hold"
          : a.is_occupied
          ? "occ"
          : "vac";
        return `
          <button class="room-tab ${isAct ? "active" : ""}" data-room-tab="${escapeHtml(rid)}">
            <span class="dot ${dotClass}"></span>
            <span>${escapeHtml(rname)}</span>
          </button>
        `;
      })
      .join("");

    const mainContentHtml = activeSensor
      ? this._renderFocusedRoomWorkspace(activeSensor, outAttr)
      : `<div class="card">No rooms configured yet. Click <b>🌍 Rooms & Schedule</b> in the top bar to add your first room.</div>`;

    const overlayHtml = this._renderActiveOverlay(activeSensor, outAttr, rooms);

    this.shadowRoot.innerHTML = `
      <style>${PANEL_STYLES}</style>
      <div class="app-shell">
        <div class="top-bar">
          <div class="brand-row">
            <button class="icon-btn" id="btn-ha-menu" title="Toggle Home Assistant Sidebar">☰</button>
            <span class="brand-title">🔆 Perceptive Lighting</span>
          </div>
          <div class="top-actions">
            <span class="ambient-badge" title="${escapeHtml(outAttr.regime_explanation || "")}">
              ${activeRegimeMeta.icon} ${activeRegimeMeta.label} • Sky ${outLux} lx (${escapeHtml(outAttr.weather_condition || "clear")})
            </span>
            <button class="btn sm" id="btn-open-hub-drawer">🌍 Rooms & Schedule</button>
          </div>
        </div>

        <div class="room-bar">
          ${roomTabsHtml}
        </div>

        ${mainContentHtml}
      </div>

      ${overlayHtml}
      ${this._toastMsg ? `<div class="toast">✓ ${escapeHtml(this._toastMsg)}</div>` : ""}
    `;

    const newDrawerEl = this.shadowRoot.querySelector(".drawer");
    if (newDrawerEl && prevDrawerScroll > 0) {
      newDrawerEl.scrollTop = prevDrawerScroll;
    }

    this._bindEvents(activeSensor, outAttr, rooms);
  }

  _renderFocusedRoomWorkspace(sensor, outAttr) {
    const attr = this._getEffectiveRoomAttr(sensor);
    const roomName = attr.room_name || attr.room_id;
    const isLocked = Boolean(attr.automation_locked);
    const isManualHold = Boolean(attr.manual_override_active);
    const isVacancyHold = Boolean(attr.vacancy_hold_active);
    const isOccupied = Boolean(attr.is_occupied);

    const totalLights = (attr.included_lights || []).length;
    const shedCount = Number(attr.active_shed_count || 0);
    const activeLightsCount = Math.max(0, totalLights - shedCount);
    const scenesMap = attr.available_scenes || {};

    let statusPillHtml = `<span class="status-pill vac">⚪ Vacant</span>`;
    if (isLocked) {
      statusPillHtml = `<span class="status-pill locked">🔒 Locked (Manual Only)</span>`;
    } else if (isManualHold) {
      statusPillHtml = `<span class="status-pill hold">✋ Manual Hold (Until Vacant)</span>`;
    } else if (isVacancyHold) {
      statusPillHtml = `<span class="status-pill hold">⏳ Occupancy Grace Hold</span>`;
    } else if (isOccupied) {
      statusPillHtml = `<span class="status-pill occ">🟢 Occupied</span>`;
    }

    // 4-Row Auto-Saving Daily Schedule Matrix
    const regimeWeights = attr.regime_weights || outAttr.regime_weights || {};
    const regimeConfigs = attr.regime_configs || {};
    const scheduleRowsHtml = Object.entries(REGIME_META)
      .map(([rkey, meta]) => {
        const rcfg = regimeConfigs[rkey] || {};
        const wPct = Math.round((Number(regimeWeights[rkey]) || 0) * 100);
        const isCurrent = wPct >= 50 || attr.time_regime === rkey;
        const occScene = rcfg.occupied_scene || "all_lights";
        const targetBr = Number(rcfg.target_brightness_pct ?? 60);
        const turnOnLux = Number(rcfg.turn_on_lux ?? 12.0);
        const offScene = rcfg.off_scene || "off";
        const hasOffOverride =
          offScene !== "off" &&
          (rcfg.off_scene_when_unoccupied || rcfg.off_scene_when_bright);
        const offBtnLabel = hasOffOverride
          ? `🎨 ${scenesMap[offScene] || offScene}`
          : "⏹️ Turn Off";

        return `
          <div class="schedule-row ${isCurrent ? "is-active" : ""}" data-regime-row="${rkey}">
            <div class="regime-name">
              <span>${meta.icon} ${meta.label}</span>
              ${
                wPct > 0
                  ? `<span class="regime-active-tag">● ${wPct}% Active</span>`
                  : `<span style="font-size:0.7rem;color:var(--secondary-text-color);">Scheduled</span>`
              }
            </div>

            <div>
              <div class="col-label"><span>When Occupied</span></div>
              <select class="auto-reg-scene" data-rkey="${rkey}">
                ${this._buildSceneOptionsHtml(scenesMap, occScene, false)}
              </select>
            </div>

            <div>
              <div class="col-label">
                <span>Brightness</span>
                <b class="lbl-reg-bri" data-rkey="${rkey}" style="color:#60a5fa;">${targetBr}%</b>
              </div>
              <input type="range" class="auto-reg-bri" data-rkey="${rkey}" min="1" max="100" step="1" value="${targetBr}" />
            </div>

            <div>
              <div class="col-label"><span>Turn On ≤ (lx)</span></div>
              <input type="number" class="auto-reg-lux" data-rkey="${rkey}" min="0.5" max="500" step="0.5" value="${turnOnLux}" />
            </div>

            <div>
              <div class="col-label"><span>When Vacant</span></div>
              <button class="btn sm btn-open-off-modal" data-rkey="${rkey}" title="Configure Vacant / Off-State Scene Override">
                ${escapeHtml(offBtnLabel)}
              </button>
            </div>
          </div>
        `;
      })
      .join("");

    // Conditional Scene Rules
    const triggers = attr.scene_triggers || [];
    const rulesHtml = triggers
      .map((t, idx) => {
        const regLbl =
          t.regime && REGIME_META[t.regime]
            ? `${REGIME_META[t.regime].icon} ${REGIME_META[t.regime].label}`
            : "🕒 Any Time";
        const isPresenceOnly = !t.entity_id || t.entity_id === "presence_only";
        const friendlyEntity = isPresenceOnly
          ? "Presence Only"
          : getFriendlyEntityName(this._hass, attr, t.entity_id);
        const statesStr = Array.isArray(t.states)
          ? t.states.join(" / ")
          : t.states || "on, playing";
        const normSceneId =
          t.scene_id && String(t.scene_id).startsWith("scene.")
            ? `ha_${String(t.scene_id).replace("scene.", "")}`
            : t.scene_id;
        const sceneLbl = scenesMap[normSceneId] || scenesMap[t.scene_id] || t.scene_id;
        const isRuleActive =
          attr.active_trigger_id &&
          (attr.active_trigger_id === t.id || attr.active_trigger_id === t.entity_id);

        return `
          <div class="rule-card ${isRuleActive ? "active-rule" : ""}">
            <div>
              <div style="font-size:0.88rem;font-weight:700;display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
                <span>${regLbl}</span>
                <span>•</span>
                <span>${
                  isPresenceOnly
                    ? "🚶 When Room is Occupied"
                    : `⚡ When <b>${escapeHtml(friendlyEntity)}</b> is <b>${escapeHtml(statesStr)}</b>`
                }</span>
                ${
                  isRuleActive
                    ? `<span class="status-pill occ" style="padding:2px 8px;font-size:0.68rem;">ACTIVE NOW</span>`
                    : ""
                }
              </div>
              <div style="font-size:0.78rem;color:var(--secondary-text-color);margin-top:3px;">
                ➔ Apply <b>${escapeHtml(sceneLbl)}</b> (${
                  t.scale_dynamically !== false ? "Adapts with Daylight" : "Fixed Scene Brightness"
                })
              </div>
            </div>
            <button class="btn danger sm btn-del-rule" data-idx="${idx}">Remove</button>
          </div>
        `;
      })
      .join("");

    const presCount = (attr.included_presence_sensors || []).length;
    const luxCount = (attr.included_lux_sensors || []).length;
    const vacDelay = Number(attr.vacancy_delay_seconds ?? 90);

    return `
      <!-- CARD 1: LIVE ROOM STATUS & TACTILE CONTROL -->
      <div class="card">
        <div class="hero-header">
          <div>
            <div class="room-heading">
              <span>${escapeHtml(roomName)}</span>
              ${statusPillHtml}
            </div>
            <div class="live-sentence">
              ${escapeHtml(attr.decision_reason || "Evaluating room lighting...")}
            </div>
          </div>

          <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
            ${
              isManualHold
                ? `<button class="btn amber sm" id="btn-resume-auto">✨ Resume Auto Now</button>`
                : ""
            }
            <button class="btn sm ${isLocked ? "danger" : ""}" id="btn-toggle-lock">
              ${isLocked ? "🔓 Unlock Room" : "🔒 Lock Manual"}
            </button>
          </div>
        </div>

        <div class="telemetry-strip">
          <span>Room Lux: <b>${attr.effective_spatial_lux ?? attr.raw_lux} lx</b> (Turn-On ≤ <b>${attr.turn_on_lux_threshold} lx</b>)</span>
          <span>•</span>
          <span>Target Brightness: <b>${attr.recommended_brightness_pct}%</b></span>
          <span>•</span>
          <span>Fixtures: <b>${activeLightsCount} / ${totalLights} active</b>${shedCount > 0 ? ` (${shedCount} shed)` : ""}</span>
          <span>•</span>
          <span>Occupancy Hold: <b>${vacDelay}s</b></span>
        </div>

        <div class="control-bar">
          <!-- Direct Brightness & Shedding Stepper -->
          <div class="control-box">
            <div class="box-label">
              <span>Direct Brightness & 1% Shedding</span>
            </div>
            <div class="stepper-row">
              <button class="btn" id="btn-hero-dimmer" title="Dims down to 1%, then turns off one fixture at a time">
                🔉 Dimmer (-)
              </button>
              <button class="btn primary" id="btn-hero-brighter" title="Restores shed fixtures or steps brightness up">
                🔆 Brighter (+)
              </button>
            </div>
          </div>

          <!-- Active Scene Switcher & Capture -->
          <div class="control-box">
            <div class="box-label">
              <span>Active Scene</span>
              <button class="btn sm" id="btn-open-capture-modal" style="padding:2px 8px;font-size:0.71rem;">
                + Save Current Lights
              </button>
            </div>
            <select id="sel-live-scene">
              <option value="default" ${attr.active_scene_id === "default" ? "selected" : ""}>
                ✨ Auto (${escapeHtml(scenesMap[attr.resolved_scene_id] || "Schedule Default")})
              </option>
              ${this._buildSceneOptionsHtml(scenesMap, attr.active_scene_id, false)}
            </select>
          </div>

          <!-- 1-Tap Threshold Calibration -->
          <div class="control-box">
            <div class="box-label">
              <span>Teach Turn-On Threshold (${attr.effective_spatial_lux ?? attr.raw_lux} lx now)</span>
            </div>
            <div class="stepper-row">
              <button class="btn" id="btn-teach-dark" title="Turn lights ON and raise Turn-On Lux above current room lux">
                🌑 Should Be On
              </button>
              <button class="btn" id="btn-teach-bright" title="Turn lights OFF and lower Turn-On Lux below current room lux">
                ☀️ Bright Enough
              </button>
            </div>
          </div>
        </div>
      </div>

      <!-- CARD 2: AUTO-SAVING 4-ROW DAILY LIGHTING SCHEDULE -->
      <div class="card">
        <div class="card-header">
          <span class="card-title">🕒 Daily Lighting Schedule</span>
          <span style="font-size:0.75rem;color:var(--secondary-text-color);">Changes save automatically</span>
        </div>
        <div class="card-desc">
          Set each time of day's default scene, brightness, and turn-on lux level. Adjacent times of day cross-fade smoothly so there are never sudden pre-sunset drops.
        </div>

        <div class="schedule-matrix">
          ${scheduleRowsHtml}
        </div>
      </div>

      <!-- CARD 3: CONDITIONAL SCENE RULES -->
      <div class="card">
        <div class="card-header">
          <span class="card-title">⚡ Conditional Scene Rules</span>
          <button class="btn primary sm" id="btn-open-rule-modal">+ Add Rule</button>
        </div>
        <div class="card-desc">
          Automatically switch scenes when a device (like a TV or Projector) turns on or plays media. Triggered rules fire immediately.
        </div>

        <div>
          ${
            rulesHtml ||
            `<div style="font-size:0.82rem;color:var(--secondary-text-color);padding:6px 0;">No conditional rules configured. The room is following its Daily Lighting Schedule above.</div>`
          }
        </div>
      </div>

      <!-- LAYER 2 SETUP DRAWER LAUNCHERS -->
      <div class="launcher-grid">
        <button class="launcher-tile" data-open-drawer="lights">
          <div>
            <div class="launcher-title">💡 Light Shedding Order</div>
            <div class="launcher-sub">${totalLights} fixtures • ${
              attr.light_order_locked ? "Custom Priority" : "Auto-Ordered"
            }</div>
          </div>
          <span>➔</span>
        </button>

        <button class="launcher-tile" data-open-drawer="sensors">
          <div>
            <div class="launcher-title">📡 Occupancy & Sensors</div>
            <div class="launcher-sub">${presCount} presence (${vacDelay}s hold) • ${luxCount} lux sensors</div>
          </div>
          <span>➔</span>
        </button>

        <button class="launcher-tile" data-open-drawer="tuning">
          <div>
            <div class="launcher-title">⚙️ Room Tuning, AI & Reset</div>
            <div class="launcher-sub">Mode, Circadian CCT, Math Models & Reset</div>
          </div>
          <span>➔</span>
        </button>
      </div>
    `;
  }

  _renderActiveOverlay(activeSensor, outAttr, rooms) {
    if (this._activeModal) {
      return this._renderModal(activeSensor);
    }
    if (!this._activeDrawer) return "";

    if (this._activeDrawer === "hub") {
      return this._renderHubDrawer(outAttr, rooms);
    }
    if (!activeSensor) return "";
    const attr = this._getEffectiveRoomAttr(activeSensor);

    if (this._activeDrawer === "lights") {
      return this._renderLightsDrawer(attr);
    }
    if (this._activeDrawer === "sensors") {
      return this._renderSensorsDrawer(attr);
    }
    if (this._activeDrawer === "tuning") {
      return this._renderTuningDrawer(attr);
    }
    return "";
  }

  _renderLightsDrawer(attr) {
    const priorityOrder = attr.light_priority_order || attr.included_lights || [];
    const lightMeta = attr.light_metadata || {};
    const shedSet = new Set(attr.shed_light_entities || []);

    const rowsHtml = priorityOrder
      .map((eid, idx) => {
        const meta = lightMeta[eid] || {};
        const fname = getFriendlyEntityName(this._hass, attr, eid);
        const st = this._hass.states[eid];
        const isOn = st && st.state === "on";
        const brVal =
          isOn && st.attributes && st.attributes.brightness
            ? `${Math.round((Number(st.attributes.brightness) / 255) * 100)}%`
            : isOn
            ? "ON"
            : "OFF";
        const isShed = shedSet.has(eid);
        const isLast = idx === priorityOrder.length - 1 && priorityOrder.length > 1;
        const roleLabel =
          idx === 0
            ? "Turns off 1st past 1%"
            : isLast
            ? "🌙 Kept on last at 1% (Nightlight)"
            : `#${idx + 1} to turn off`;

        return `
          <div class="list-row ${isLast ? "is-last-light" : ""}">
            <div style="display:flex;align-items:center;gap:10px;min-width:0;">
              <span class="rank-badge">#${idx + 1}</span>
              <div style="min-width:0;">
                <div style="font-weight:700;font-size:0.88rem;">
                  ${escapeHtml(fname)}
                  <span style="font-size:0.74rem;font-weight:600;color:${
                    isShed ? "#fbbf24" : isOn ? "#34d399" : "#94a3b8"
                  };margin-left:6px;">
                    • ${isShed ? "Shed (Off)" : brVal}
                  </span>
                </div>
                <div style="font-size:0.74rem;color:var(--secondary-text-color);margin-top:2px;">
                  ${escapeHtml(meta.category_label || "Fixture")} • <b>${roleLabel}</b>
                </div>
              </div>
            </div>
            <div style="display:flex;gap:6px;flex-shrink:0;">
              <button class="btn sm btn-move-light-up" data-idx="${idx}" ${
                idx === 0 ? "disabled" : ""
              } title="Turn off earlier">⬆️</button>
              <button class="btn sm btn-move-light-down" data-idx="${idx}" ${
                idx === priorityOrder.length - 1 ? "disabled" : ""
              } title="Keep on longer">⬇️</button>
            </div>
          </div>
        `;
      })
      .join("");

    return `
      <div class="backdrop" id="overlay-backdrop">
        <div class="drawer" onclick="event.stopPropagation()">
          <div class="drawer-header">
            <span class="drawer-title">💡 Light Shedding Order — ${escapeHtml(attr.room_name)}</span>
            <button class="btn sm" id="btn-close-overlay">✕ Done</button>
          </div>

          <div class="card-desc">
            When you dim down to <b>1%</b> and press <b>Dimmer (-)</b> again, Perceptive Lighting turns off <b>one fixture at a time</b> from #1 downward instead of plunging the room into darkness. Put switches and ceiling flood/main lights at the top (#1) and your softest accent lamp at the bottom.
          </div>

          <div style="display:flex;justify-content:flex-end;margin-bottom:10px;">
            ${
              attr.light_order_locked
                ? `<button class="btn sm" id="btn-reset-light-order">🔓 Reset to Automatic Fixture Order</button>`
                : `<span style="font-size:0.76rem;color:var(--secondary-text-color);">🤖 Automatically ordered by fixture type</span>`
            }
          </div>

          <div>${rowsHtml || "<div>No lights included in this room.</div>"}</div>
        </div>
      </div>
    `;
  }

  _renderSensorsDrawer(attr) {
    const allArea = attr.all_area_entities || {};
    const excludedSet = new Set(attr.excluded_entities || []);
    const sProfiles = attr.sensor_profiles || {};
    const sBreakdown = attr.sensor_breakdown || {};
    const vacDelay = Number(attr.vacancy_delay_seconds ?? 90);

    // 1. Occupancy / Presence sensors (including FP2 zones & groups — never locked out!)
    const presList = allArea.presence_sensors || [];
    const presHtml = presList
      .map((eid) => {
        const isInc = !excludedSet.has(eid);
        const fname = getFriendlyEntityName(this._hass, attr, eid);
        const st = this._hass.states[eid];
        const isOn = st && st.state === "on";
        return `
          <label class="list-row" style="cursor:pointer;">
            <div>
              <div style="font-weight:700;font-size:0.86rem;">
                ${escapeHtml(fname)}
                <span style="font-size:0.74rem;margin-left:6px;color:${
                  isOn ? "#34d399" : "#94a3b8"
                };">
                  ● ${isOn ? "Detected" : "Clear"}
                </span>
              </div>
              <div style="font-size:0.72rem;color:var(--secondary-text-color);">${escapeHtml(eid)}</div>
            </div>
            <input type="checkbox" class="chk-toggle-entity" data-eid="${escapeHtml(eid)}" ${
              isInc ? "checked" : ""
            } />
          </label>
        `;
      })
      .join("");

    // 2. Illuminance (Lux) sensors with Role / Placement Selector
    const luxList = allArea.illuminance_sensors || [];
    const luxHtml = luxList
      .map((eid) => {
        const isInc = !excludedSet.has(eid);
        const fname = getFriendlyEntityName(this._hass, attr, eid);
        const prof = sProfiles[eid] || {};
        const curPlace = prof.placement || "auto_learn";
        const st = this._hass.states[eid];
        const rawVal = st ? `${Math.round(Number(st.state) * 10) / 10} lx` : "--";
        const calVal = sBreakdown[eid] !== undefined ? `${sBreakdown[eid]} lx` : rawVal;

        return `
          <div class="list-row" style="flex-wrap:wrap;">
            <div style="flex:1;min-width:180px;">
              <label style="display:flex;align-items:center;gap:8px;cursor:pointer;font-weight:700;font-size:0.86rem;">
                <input type="checkbox" class="chk-toggle-entity" data-eid="${escapeHtml(eid)}" ${
                  isInc ? "checked" : ""
                } />
                <span>${escapeHtml(fname)}</span>
                <span style="color:#60a5fa;font-size:0.78rem;">${escapeHtml(calVal)}</span>
              </label>
              <div style="font-size:0.72rem;color:var(--secondary-text-color);margin-top:2px;">
                ${escapeHtml(prof.ai_location_guess || eid)}
              </div>
            </div>
            <div style="width:210px;">
              <select class="sel-sensor-placement" data-seid="${escapeHtml(eid)}">
                ${Object.entries(SENSOR_PLACEMENT_OPTIONS)
                  .map(
                    ([k, lbl]) =>
                      `<option value="${k}" ${k === curPlace ? "selected" : ""}>${escapeHtml(lbl)}</option>`
                  )
                  .join("")}
              </select>
            </div>
          </div>
        `;
      })
      .join("");

    // 3. Lights & Media Players inclusion toggles
    const otherEntities = [
      ...(allArea.lights || []),
      ...(allArea.media_players || []),
    ];
    const otherHtml = otherEntities
      .map((eid) => {
        const isInc = !excludedSet.has(eid);
        const fname = getFriendlyEntityName(this._hass, attr, eid);
        const st = this._hass.states[eid];
        return `
          <label class="list-row" style="cursor:pointer;">
            <div>
              <div style="font-weight:700;font-size:0.85rem;">${escapeHtml(fname)}</div>
              <div style="font-size:0.72rem;color:var(--secondary-text-color);">
                ${escapeHtml(eid)} • State: <b>${escapeHtml(st ? st.state : "unknown")}</b>
              </div>
            </div>
            <input type="checkbox" class="chk-toggle-entity" data-eid="${escapeHtml(eid)}" ${
              isInc ? "checked" : ""
            } />
          </label>
        `;
      })
      .join("");

    return `
      <div class="backdrop" id="overlay-backdrop">
        <div class="drawer" onclick="event.stopPropagation()">
          <div class="drawer-header">
            <span class="drawer-title">📡 Occupancy & Sensors — ${escapeHtml(attr.room_name)}</span>
            <button class="btn sm" id="btn-close-overlay">✕ Done</button>
          </div>

          <div class="section-label">1. Occupancy Hold Timer (Anti-Dropout)</div>
          <div class="card-desc">
            Keep the room occupied for this grace period after motion/radar sensors clear so lights never turn off while you are sitting still.
          </div>
          <select id="sel-vacancy-delay">
            ${VACANCY_DELAY_PRESETS.map(
              (p) =>
                `<option value="${p.value}" ${p.value === vacDelay ? "selected" : ""}>${escapeHtml(
                  p.label
                )}</option>`
            ).join("")}
          </select>

          <div class="section-label">2. Presence, Radar & Occupancy Sensors</div>
          <div class="card-desc">
            Check the sensors and zones that should keep <b>${escapeHtml(attr.room_name)}</b> occupied.
          </div>
          <div>${presHtml || "<div>No presence sensors discovered in this Area.</div>"}</div>

          <div class="section-label">3. Illuminance (Lux) Sensors & Windowsill Role</div>
          <div class="card-desc">
            Select which light sensors measure this room and adjust their placement role if a windowsill sensor needs glare dampening.
          </div>
          <div>${luxHtml || "<div>No illuminance sensors discovered in this Area.</div>"}</div>

          <div class="section-label">4. Controlled Lights & Media Players</div>
          <div class="card-desc">
            Uncheck any duplicate or unwanted light entities so Perceptive Lighting ignores them.
          </div>
          <div>${otherHtml}</div>
        </div>
      </div>
    `;
  }

  _renderTuningDrawer(attr) {
    const lwModels = (attr.learned_weights && attr.learned_weights.regime_models) || {};
    const curReg = attr.time_regime || "evening";
    const mSpec = lwModels[curReg] || {};
    const curIllum = mSpec.illum_model || attr.active_illum_model || "weber_fechner";
    const curWeather = mSpec.weather_model || attr.active_weather_model || "cave_contrast";
    const curSeason = mSpec.season_model || attr.active_season_model || "season_neutral";

    return `
      <div class="backdrop" id="overlay-backdrop">
        <div class="drawer" onclick="event.stopPropagation()">
          <div class="drawer-header">
            <span class="drawer-title">⚙️ Room Tuning & AI — ${escapeHtml(attr.room_name)}</span>
            <button class="btn sm" id="btn-close-overlay">✕ Done</button>
          </div>

          <div class="section-label">Controller Behavior (Auto-Saving)</div>
          <div class="list-row">
            <div>
              <div style="font-weight:700;font-size:0.86rem;">Operating Mode</div>
              <div style="font-size:0.74rem;color:var(--secondary-text-color);">Full adaptive, simple transit, or passive sensor-only</div>
            </div>
            <div style="width:210px;">
              <select id="tune-op-mode">
                <option value="full_adaptive" ${attr.operating_mode === "full_adaptive" ? "selected" : ""}>Full Adaptive Controller</option>
                <option value="simple_transit" ${attr.operating_mode === "simple_transit" ? "selected" : ""}>Simple Transit</option>
                <option value="sensor_only" ${attr.operating_mode === "sensor_only" ? "selected" : ""}>Sensor-Only (Passive)</option>
              </select>
            </div>
          </div>

          <div class="list-row">
            <div>
              <div style="font-weight:700;font-size:0.86rem;">Color Temperature (CCT)</div>
              <div style="font-size:0.74rem;color:var(--secondary-text-color);">Let scenes manage CCT or use Kruithof circadian curve</div>
            </div>
            <div style="width:210px;">
              <select id="tune-cct-mode">
                <option value="external" ${attr.cct_mode === "external" ? "selected" : ""}>Compatible / Scene CCT</option>
                <option value="kruithof_circadian" ${attr.cct_mode === "kruithof_circadian" ? "selected" : ""}>Built-in Kruithof Circadian CCT</option>
                <option value="brightness_only" ${attr.cct_mode === "brightness_only" ? "selected" : ""}>Brightness Only</option>
              </select>
            </div>
          </div>

          <div class="list-row">
            <div>
              <div style="font-weight:700;font-size:0.86rem;">On/Off Anti-Flicker Cooldown (Minutes)</div>
              <div style="font-size:0.74rem;color:var(--secondary-text-color);">Prevents rapid on/off toggling while occupied</div>
            </div>
            <div style="width:110px;">
              <input type="number" id="tune-cooldown" min="0" max="30" step="0.5" value="${attr.on_off_cooldown_minutes ?? 3}" />
            </div>
          </div>

          <div class="list-row">
            <div>
              <div style="font-weight:700;font-size:0.86rem;">Gradual Daylight Adjust Interval (Minutes)</div>
              <div style="font-size:0.74rem;color:var(--secondary-text-color);">How often ongoing daylight brightness updates apply</div>
            </div>
            <div style="width:110px;">
              <input type="number" id="tune-adjust" min="0.5" max="30" step="0.5" value="${attr.adjust_interval_minutes ?? 2}" />
            </div>
          </div>

          <div class="section-label">Active Perceptual Adaptation Models (${escapeHtml(curReg.toUpperCase())})</div>
          <div class="list-row">
            <span style="font-size:0.84rem;font-weight:600;">Illuminance Curve</span>
            <div style="width:230px;">
              <select id="tune-illum-model">
                ${Object.entries(ILLUM_MODEL_OPTIONS)
                  .map(([k, lbl]) => `<option value="${k}" ${k === curIllum ? "selected" : ""}>${escapeHtml(lbl)}</option>`)
                  .join("")}
              </select>
            </div>
          </div>
          <div class="list-row">
            <span style="font-size:0.84rem;font-weight:600;">Weather Contrast Model</span>
            <div style="width:230px;">
              <select id="tune-weather-model">
                ${Object.entries(WEATHER_MODEL_OPTIONS)
                  .map(([k, lbl]) => `<option value="${k}" ${k === curWeather ? "selected" : ""}>${escapeHtml(lbl)}</option>`)
                  .join("")}
              </select>
            </div>
          </div>
          <div class="list-row">
            <span style="font-size:0.84rem;font-weight:600;">Seasonal & Thermal Model</span>
            <div style="width:230px;">
              <select id="tune-season-model">
                ${Object.entries(SEASON_MODEL_OPTIONS)
                  .map(([k, lbl]) => `<option value="${k}" ${k === curSeason ? "selected" : ""}>${escapeHtml(lbl)}</option>`)
                  .join("")}
              </select>
            </div>
          </div>

          <div class="section-label">AI Calibration & Room Reset</div>
          <div class="card-desc">
            ${escapeHtml(attr.ai_insight || "Use AI to order fixture shedding priority and select adaptation models.")}
          </div>
          <div style="display:flex;gap:10px;flex-wrap:wrap;">
            <button class="btn primary" id="btn-run-ai">🧠 Run AI Calibration</button>
            <button class="btn danger" id="btn-reset-this-room">🧹 Reset Room to Clean Defaults</button>
          </div>
        </div>
      </div>
    `;
  }

  _renderHubDrawer(outAttr, rooms) {
    const sched = outAttr.regime_schedule || {};
    const mode = sched.mode || "hybrid";
    const transMin = sched.transition_minutes ?? 45;
    const morningTime = decimalHourToTimeStr(sched.morning_start_hour ?? 6.0);
    const morningEl = sched.morning_solar_elev ?? -4.0;
    const dayTime = decimalHourToTimeStr(sched.day_start_hour ?? 10.0);
    const dayEl = sched.day_solar_elev ?? 14.0;
    const eveTime = decimalHourToTimeStr(sched.evening_start_hour ?? 17.5);
    const eveEl = sched.evening_solar_elev ?? 9.0;
    const ovnStartTime = decimalHourToTimeStr(sched.overnight_start_hour ?? 23.0);
    const ovnEndTime = decimalHourToTimeStr(sched.overnight_end_hour ?? 6.0);
    const gloomShift = sched.gloom_early_evening !== false;
    const gloomLux = sched.gloom_sky_lux_threshold ?? 1500;

    const configuredIds = new Set(rooms.map((r) => r.attributes.room_id));
    const allAreas = outAttr.all_ha_areas || [];
    const unconfiguredAreas = allAreas.filter((a) => !configuredIds.has(a.area_id));

    return `
      <div class="backdrop" id="overlay-backdrop">
        <div class="drawer" onclick="event.stopPropagation()">
          <div class="drawer-header">
            <span class="drawer-title">🌍 Rooms & Global Time-of-Day Schedule</span>
            <button class="btn sm" id="btn-close-overlay">✕ Done</button>
          </div>

          <div class="section-label">1. Configured Rooms (Home Assistant Areas)</div>
          <div style="display:flex;gap:8px;margin-bottom:12px;">
            <select id="hub-add-area">
              ${
                unconfiguredAreas.length > 0
                  ? unconfiguredAreas
                      .map((a) => `<option value="${escapeHtml(a.area_id)}">${escapeHtml(a.name)}</option>`)
                      .join("")
                  : `<option value="">All Home Assistant Areas added</option>`
              }
            </select>
            <button class="btn primary" id="btn-add-area" style="white-space:nowrap;" ${
              unconfiguredAreas.length === 0 ? "disabled" : ""
            }>+ Add Room</button>
          </div>

          <div>
            ${rooms
              .map((r) => {
                const a = r.attributes;
                return `
                  <div class="list-row">
                    <div>
                      <div style="font-weight:700;font-size:0.88rem;">${escapeHtml(a.room_name || a.room_id)}</div>
                      <div style="font-size:0.74rem;color:var(--secondary-text-color);">
                        ${(a.included_lights || []).length} lights • ${(a.included_presence_sensors || []).length} presence • ${(a.included_lux_sensors || []).length} lux
                      </div>
                    </div>
                    <button class="btn danger sm btn-del-room" data-room="${escapeHtml(a.room_id)}">Remove</button>
                  </div>
                `;
              })
              .join("")}
          </div>

          <div class="section-label">2. Global Time-of-Day Boundaries (Auto-Saving)</div>
          <div class="card-desc">
            Controls when Morning, Day, Evening, and Overnight occur across your home. Adjacent regimes blend smoothly over the cross-fade window.
          </div>

          <div class="list-row">
            <span style="font-size:0.85rem;font-weight:600;">Detection Mode</span>
            <div style="width:240px;">
              <select class="auto-hub-sched" id="hub-mode">
                <option value="hybrid" ${mode === "hybrid" ? "selected" : ""}>Hybrid (Clock + Sun + Weather)</option>
                <option value="clock_only" ${mode === "clock_only" ? "selected" : ""}>Clock Schedule Only</option>
                <option value="solar_only" ${mode === "solar_only" ? "selected" : ""}>Solar Elevation Only</option>
              </select>
            </div>
          </div>

          <div class="list-row">
            <span style="font-size:0.85rem;font-weight:600;">Smooth Cross-Fade Window (Minutes)</span>
            <div style="width:120px;">
              <input type="number" class="auto-hub-sched" id="hub-trans-min" min="5" max="120" step="5" value="${transMin}" />
            </div>
          </div>

          <div class="list-row">
            <div>
              <div style="font-size:0.85rem;font-weight:600;">🌅 Morning Starts</div>
              <div style="font-size:0.72rem;color:var(--secondary-text-color);">Clock Time & Dawn Sun Elevation (°)</div>
            </div>
            <div style="display:flex;gap:6px;width:210px;">
              <input type="time" class="auto-hub-sched" id="hub-morn-time" value="${morningTime}" />
              <input type="number" class="auto-hub-sched" id="hub-morn-el" step="0.5" value="${morningEl}" title="Sun Elevation (°)" />
            </div>
          </div>

          <div class="list-row">
            <div>
              <div style="font-size:0.85rem;font-weight:600;">☀️ Day Starts</div>
              <div style="font-size:0.72rem;color:var(--secondary-text-color);">Clock Time & Sun Elevation (°)</div>
            </div>
            <div style="display:flex;gap:6px;width:210px;">
              <input type="time" class="auto-hub-sched" id="hub-day-time" value="${dayTime}" />
              <input type="number" class="auto-hub-sched" id="hub-day-el" step="0.5" value="${dayEl}" title="Sun Elevation (°)" />
            </div>
          </div>

          <div class="list-row">
            <div>
              <div style="font-size:0.85rem;font-weight:600;">🌆 Evening Starts</div>
              <div style="font-size:0.72rem;color:var(--secondary-text-color);">Clock Time & Pre-Sunset Sun Elevation (°)</div>
            </div>
            <div style="display:flex;gap:6px;width:210px;">
              <input type="time" class="auto-hub-sched" id="hub-eve-time" value="${eveTime}" />
              <input type="number" class="auto-hub-sched" id="hub-eve-el" step="0.5" value="${eveEl}" title="Sun Elevation (°)" />
            </div>
          </div>

          <div class="list-row">
            <div>
              <div style="font-size:0.85rem;font-weight:600;">🌙 Overnight Window</div>
              <div style="font-size:0.72rem;color:var(--secondary-text-color);">Late Night Start → Morning End</div>
            </div>
            <div style="display:flex;gap:6px;width:210px;">
              <input type="time" class="auto-hub-sched" id="hub-ovn-start" value="${ovnStartTime}" />
              <input type="time" class="auto-hub-sched" id="hub-ovn-end" value="${ovnEndTime}" />
            </div>
          </div>

          <div class="list-row">
            <div>
              <label style="display:flex;align-items:center;gap:8px;font-size:0.85rem;font-weight:600;cursor:pointer;">
                <input type="checkbox" class="auto-hub-sched" id="hub-gloom-chk" ${gloomShift ? "checked" : ""} />
                <span>Overcast Afternoon Early-Evening Shift</span>
              </label>
              <div style="font-size:0.72rem;color:var(--secondary-text-color);">Blend into Evening earlier when rainy/overcast sky lux drops below threshold</div>
            </div>
            <div style="width:110px;">
              <input type="number" class="auto-hub-sched" id="hub-gloom-lux" min="100" max="10000" step="100" value="${gloomLux}" />
            </div>
          </div>

          <div class="section-label">3. Global Reset</div>
          <button class="btn danger" id="btn-reset-all">🧹 Reset All Rooms to Factory Defaults</button>
        </div>
      </div>
    `;
  }

  _renderModal(activeSensor) {
    if (!activeSensor) return "";
    const attr = this._getEffectiveRoomAttr(activeSensor);
    const scenesMap = attr.available_scenes || {};

    if (this._activeModal === "capture_scene") {
      return `
        <div class="backdrop center-modal" id="overlay-backdrop">
          <div class="modal-box" onclick="event.stopPropagation()">
            <div class="drawer-header">
              <span class="drawer-title">📸 Save Current Lights as Scene</span>
              <button class="btn sm" id="btn-close-overlay">✕</button>
            </div>
            <div class="card-desc">
              Snapshots the current on/off, brightness, and color of every light in <b>${escapeHtml(
                attr.room_name
              )}</b> as a reusable adaptive scene.
            </div>
            <div style="margin-bottom:14px;">
              <div class="col-label"><span>Scene Name</span></div>
              <input type="text" id="inp-capture-scene-name" placeholder="e.g. Cosy Evening, Movie Mode" />
            </div>
            <div style="display:flex;justify-content:flex-end;gap:8px;">
              <button class="btn" id="btn-close-overlay-2">Cancel</button>
              <button class="btn primary" id="btn-confirm-capture-scene">Save Scene</button>
            </div>
          </div>
        </div>
      `;
    }

    if (this._activeModal && this._activeModal.type === "off_scene") {
      const rkey = this._activeModal.regime || "evening";
      const meta = REGIME_META[rkey] || REGIME_META.evening;
      const rcfg = (attr.regime_configs && attr.regime_configs[rkey]) || {};
      const offScene = rcfg.off_scene || "off";
      const offUnocc = Boolean(rcfg.off_scene_when_unoccupied);
      const offBright = Boolean(rcfg.off_scene_when_bright);

      return `
        <div class="backdrop center-modal" id="overlay-backdrop">
          <div class="modal-box" onclick="event.stopPropagation()">
            <div class="drawer-header">
              <span class="drawer-title">${meta.icon} ${meta.label} — Vacant / Off Behavior</span>
              <button class="btn sm" id="btn-close-overlay">✕</button>
            </div>
            <div class="card-desc">
              Choose whether lights turn completely off when <b>${escapeHtml(
                attr.room_name
              )}</b> is vacant or bright enough, or switch to a background scene (Scene B).
            </div>
            <div style="margin-bottom:14px;">
              <div class="col-label"><span>Vacant / Off-State Scene</span></div>
              <select id="modal-off-scene" data-rkey="${rkey}">
                ${this._buildSceneOptionsHtml(scenesMap, offScene, true)}
              </select>
            </div>
            <div style="display:flex;flex-direction:column;gap:8px;margin-bottom:16px;font-size:0.84rem;">
              <label style="display:flex;align-items:center;gap:8px;cursor:pointer;">
                <input type="checkbox" id="modal-off-unocc" ${offUnocc ? "checked" : ""} />
                <span>Apply this scene when the room is <b>Unoccupied (Vacant)</b></span>
              </label>
              <label style="display:flex;align-items:center;gap:8px;cursor:pointer;">
                <input type="checkbox" id="modal-off-bright" ${offBright ? "checked" : ""} />
                <span>Apply this scene when occupied and <b>Daylight is Bright Enough</b></span>
              </label>
            </div>
            <div style="display:flex;justify-content:flex-end;gap:8px;">
              <button class="btn primary" id="btn-save-off-modal" data-rkey="${rkey}">Done</button>
            </div>
          </div>
        </div>
      `;
    }

    if (this._activeModal === "add_rule") {
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

      return `
        <div class="backdrop center-modal" id="overlay-backdrop">
          <div class="modal-box" onclick="event.stopPropagation()">
            <div class="drawer-header">
              <span class="drawer-title">⚡ Add Conditional Scene Rule</span>
              <button class="btn sm" id="btn-close-overlay">✕</button>
            </div>

            <div style="display:flex;flex-direction:column;gap:12px;">
              <div>
                <div class="col-label"><span>1. Time of Day</span></div>
                <select id="rule-regime">
                  <option value="any">🕒 Any Time of Day</option>
                  <option value="morning">🌅 Morning Only</option>
                  <option value="day">☀️ Day Only</option>
                  <option value="evening">🌆 Evening Only</option>
                  <option value="overnight">🌙 Overnight Only</option>
                </select>
              </div>

              <div>
                <div class="col-label"><span>2. Trigger Condition</span></div>
                <select id="rule-trigger-type">
                  <option value="entity" ${
                    this._newRuleTriggerType === "entity" ? "selected" : ""
                  }>⚡ When a Device / Entity is Active (e.g. TV Playing)</option>
                  <option value="presence_only" ${
                    this._newRuleTriggerType === "presence_only" ? "selected" : ""
                  }>🚶 Presence Only (Whenever Room is Occupied)</option>
                </select>
              </div>

              ${
                this._newRuleTriggerType === "entity"
                  ? `
                <div>
                  <div class="col-label"><span>Device / Entity</span></div>
                  <select id="rule-entity">
                    ${candidateTriggerEntities
                      .map((eid) => {
                        const fn = getFriendlyEntityName(this._hass, attr, eid);
                        return `<option value="${escapeHtml(eid)}">${escapeHtml(fn)} (${escapeHtml(
                          eid
                        )})</option>`;
                      })
                      .join("")}
                  </select>
                </div>
                <div>
                  <div class="col-label"><span>Active State(s) (comma-separated)</span></div>
                  <input type="text" id="rule-states" value="playing, on" />
                </div>
              `
                  : ""
              }

              <div>
                <div class="col-label"><span>3. Scene to Activate</span></div>
                <select id="rule-scene">
                  ${this._buildSceneOptionsHtml(scenesMap, "all_lights", false)}
                </select>
              </div>

              <label style="display:flex;align-items:center;gap:8px;font-size:0.83rem;cursor:pointer;margin-top:4px;">
                <input type="checkbox" id="rule-dynamic" checked />
                <span>Adapt scene brightness dynamically with real-world daylight</span>
              </label>

              <div style="display:flex;justify-content:flex-end;gap:8px;margin-top:8px;">
                <button class="btn" id="btn-close-overlay-2">Cancel</button>
                <button class="btn primary" id="btn-confirm-add-rule">Add Rule</button>
              </div>
            </div>
          </div>
        </div>
      `;
    }

    return "";
  }

  _bindEvents(sensor, outAttr, rooms) {
    // Top bar & navigation
    this.shadowRoot
      .querySelector("#btn-ha-menu")
      ?.addEventListener("click", () => {
        this.dispatchEvent(
          new Event("hass-toggle-menu", { bubbles: true, composed: true })
        );
      });

    this.shadowRoot
      .querySelector("#btn-open-hub-drawer")
      ?.addEventListener("click", () => {
        this._activeDrawer = "hub";
        this._activeModal = null;
        this._render();
      });

    this.shadowRoot.querySelectorAll("[data-room-tab]").forEach((btn) => {
      btn.addEventListener("click", () => {
        this._activeTab = btn.getAttribute("data-room-tab");
        this._render();
      });
    });

    this.shadowRoot.querySelectorAll("[data-open-drawer]").forEach((btn) => {
      btn.addEventListener("click", () => {
        this._activeDrawer = btn.getAttribute("data-open-drawer");
        this._activeModal = null;
        this._render();
      });
    });

    // Close overlay (drawer or modal)
    const closeOverlay = () => {
      this._activeDrawer = null;
      this._activeModal = null;
      this._render();
    };
    this.shadowRoot
      .querySelector("#overlay-backdrop")
      ?.addEventListener("click", closeOverlay);
    this.shadowRoot
      .querySelector("#btn-close-overlay")
      ?.addEventListener("click", closeOverlay);
    this.shadowRoot
      .querySelector("#btn-close-overlay-2")
      ?.addEventListener("click", closeOverlay);

    if (this._activeDrawer === "hub") {
      this._bindHubDrawerEvents(outAttr);
    }

    if (!sensor) return;
    const attr = this._getEffectiveRoomAttr(sensor);
    const roomId = attr.room_id;
    const roomName = attr.room_name || roomId;
    const isLocked = Boolean(attr.automation_locked);

    // Card 1: Live controls
    this.shadowRoot
      .querySelector("#btn-hero-dimmer")
      ?.addEventListener("click", () => {
        this._callService(
          "nudge_feel",
          { room_id: roomId, direction: "too_bright", step_pct: 10 },
          "Stepped dimmer (-)"
        );
      });

    this.shadowRoot
      .querySelector("#btn-hero-brighter")
      ?.addEventListener("click", () => {
        this._callService(
          "nudge_feel",
          { room_id: roomId, direction: "more_light", step_pct: 10 },
          "Stepped brighter (+)"
        );
      });

    this.shadowRoot
      .querySelector("#btn-teach-dark")
      ?.addEventListener("click", () => {
        this._callService(
          "calibrate_feels_dark_now",
          { room_id: roomId },
          "Turned lights ON & raised Turn-On threshold"
        );
      });

    this.shadowRoot
      .querySelector("#btn-teach-bright")
      ?.addEventListener("click", () => {
        this._callService(
          "calibrate_feels_bright_now",
          { room_id: roomId },
          "Turned lights OFF & lowered Turn-On threshold"
        );
      });

    this.shadowRoot
      .querySelector("#btn-resume-auto")
      ?.addEventListener("click", () => {
        this._applyOptimisticPatch(roomId, { manual_override_active: false });
        this._callService(
          "unlock_room",
          { room_id: roomId },
          `Resumed automatic control for ${roomName}`
        );
      });

    this.shadowRoot
      .querySelector("#btn-toggle-lock")
      ?.addEventListener("click", () => {
        this._applyOptimisticPatch(roomId, {
          automation_locked: !isLocked,
          manual_override_active: false,
        });
        this._callService(
          isLocked ? "unlock_room" : "lock_room",
          { room_id: roomId },
          isLocked ? `${roomName} unlocked` : `${roomName} locked to manual control`
        );
      });

    this.shadowRoot
      .querySelector("#sel-live-scene")
      ?.addEventListener("change", (ev) => {
        const val = ev.target.value;
        this._applyOptimisticPatch(roomId, { active_scene_id: val });
        this._callService(
          "apply_adaptive_scene",
          { room_id: roomId, scene: val },
          `Switched scene`
        );
      });

    this.shadowRoot
      .querySelector("#btn-open-capture-modal")
      ?.addEventListener("click", () => {
        this._activeModal = "capture_scene";
        this._render();
      });

    this.shadowRoot
      .querySelector("#btn-confirm-capture-scene")
      ?.addEventListener("click", () => {
        const sname = this.shadowRoot
          .querySelector("#inp-capture-scene-name")
          ?.value?.trim();
        if (sname) {
          this._activeModal = null;
          this._callService(
            "capture_adaptive_scene",
            { room_id: roomId, name: sname },
            `Saved Scene "${sname}"`
          );
        }
      });

    // Card 2: Auto-Saving 4-Row Daily Schedule Matrix
    const saveRegimePatch = (rkey, fieldUpdates, toastLabel) => {
      const nextConfigs = JSON.parse(JSON.stringify(attr.regime_configs || {}));
      nextConfigs[rkey] = {
        ...(nextConfigs[rkey] || {}),
        ...fieldUpdates,
      };
      this._applyOptimisticPatch(roomId, { regime_configs: nextConfigs });
      this._callService(
        "update_room_config",
        { room_id: roomId, regime_configs: nextConfigs },
        toastLabel
      );
    };

    this.shadowRoot.querySelectorAll(".auto-reg-scene").forEach((sel) => {
      sel.addEventListener("change", (ev) => {
        const rkey = sel.getAttribute("data-rkey");
        saveRegimePatch(
          rkey,
          { occupied_scene: ev.target.value },
          `Updated ${REGIME_META[rkey]?.label || rkey} scene`
        );
      });
    });

    this.shadowRoot.querySelectorAll(".auto-reg-bri").forEach((rng) => {
      rng.addEventListener("input", (ev) => {
        const rkey = rng.getAttribute("data-rkey");
        const lbl = this.shadowRoot.querySelector(
          `.lbl-reg-bri[data-rkey="${rkey}"]`
        );
        if (lbl) lbl.textContent = `${ev.target.value}%`;
      });
      rng.addEventListener("change", (ev) => {
        const rkey = rng.getAttribute("data-rkey");
        const newBri = parseInt(ev.target.value, 10) || 60;
        saveRegimePatch(
          rkey,
          { target_brightness_pct: newBri },
          `Saved ${REGIME_META[rkey]?.label || rkey} brightness (${newBri}%)`
        );
      });
    });

    this.shadowRoot.querySelectorAll(".auto-reg-lux").forEach((inp) => {
      inp.addEventListener("change", (ev) => {
        const rkey = inp.getAttribute("data-rkey");
        const newLux = parseFloat(ev.target.value) || 12.0;
        saveRegimePatch(
          rkey,
          { turn_on_lux: newLux },
          `Saved ${REGIME_META[rkey]?.label || rkey} turn-on lux (≤ ${newLux} lx)`
        );
      });
    });

    this.shadowRoot.querySelectorAll(".btn-open-off-modal").forEach((btn) => {
      btn.addEventListener("click", () => {
        const rkey = btn.getAttribute("data-rkey");
        this._activeModal = { type: "off_scene", regime: rkey };
        this._render();
      });
    });

    this.shadowRoot
      .querySelector("#btn-save-off-modal")
      ?.addEventListener("click", (ev) => {
        const rkey = ev.target.getAttribute("data-rkey");
        const offScene =
          this.shadowRoot.querySelector("#modal-off-scene")?.value || "off";
        const offUnocc = Boolean(
          this.shadowRoot.querySelector("#modal-off-unocc")?.checked
        );
        const offBright = Boolean(
          this.shadowRoot.querySelector("#modal-off-bright")?.checked
        );
        this._activeModal = null;
        saveRegimePatch(
          rkey,
          {
            off_scene: offScene,
            off_scene_when_unoccupied: offUnocc,
            off_scene_when_bright: offBright,
          },
          `Saved ${REGIME_META[rkey]?.label || rkey} vacant behavior`
        );
      });

    // Card 3: Conditional Rules & Modal Rule Builder
    this.shadowRoot
      .querySelector("#btn-open-rule-modal")
      ?.addEventListener("click", () => {
        this._activeModal = "add_rule";
        this._render();
      });

    this.shadowRoot
      .querySelector("#rule-trigger-type")
      ?.addEventListener("change", (ev) => {
        this._newRuleTriggerType = ev.target.value;
        this._render();
      });

    this.shadowRoot
      .querySelector("#btn-confirm-add-rule")
      ?.addEventListener("click", () => {
        const regime =
          this.shadowRoot.querySelector("#rule-regime")?.value || "any";
        const trigType =
          this.shadowRoot.querySelector("#rule-trigger-type")?.value || "entity";
        const entityId =
          trigType === "presence_only"
            ? "presence_only"
            : this.shadowRoot.querySelector("#rule-entity")?.value ||
              "presence_only";
        const rawStates =
          this.shadowRoot.querySelector("#rule-states")?.value || "playing, on";
        const sceneId =
          this.shadowRoot.querySelector("#rule-scene")?.value || "all_lights";
        const scaleDyn = Boolean(
          this.shadowRoot.querySelector("#rule-dynamic")?.checked
        );

        const nextTriggers = [...(attr.scene_triggers || [])];
        nextTriggers.push({
          id: `trig_${Date.now()}`,
          regime,
          entity_id: entityId,
          states:
            entityId === "presence_only"
              ? ["occupied"]
              : rawStates
                  .split(",")
                  .map((s) => s.trim())
                  .filter(Boolean),
          scene_id: sceneId,
          scale_dynamically: scaleDyn,
        });

        this._activeModal = null;
        this._applyOptimisticPatch(roomId, { scene_triggers: nextTriggers });
        this._callService(
          "update_room_config",
          { room_id: roomId, scene_triggers: nextTriggers },
          "Added conditional scene rule"
        );
      });

    this.shadowRoot.querySelectorAll(".btn-del-rule").forEach((btn) => {
      btn.addEventListener("click", () => {
        const idx = parseInt(btn.getAttribute("data-idx"), 10);
        const nextTriggers = [...(attr.scene_triggers || [])];
        if (!isNaN(idx) && idx >= 0 && idx < nextTriggers.length) {
          nextTriggers.splice(idx, 1);
          this._applyOptimisticPatch(roomId, { scene_triggers: nextTriggers });
          this._callService(
            "update_room_config",
            { room_id: roomId, scene_triggers: nextTriggers },
            "Removed conditional rule"
          );
        }
      });
    });

    // Drawer A: Light Shedding Order
    const priorityOrder = [
      ...(attr.light_priority_order || attr.included_lights || []),
    ];
    this.shadowRoot.querySelectorAll(".btn-move-light-up").forEach((btn) => {
      btn.addEventListener("click", () => {
        const idx = parseInt(btn.getAttribute("data-idx"), 10);
        if (idx > 0 && idx < priorityOrder.length) {
          const nextOrder = [...priorityOrder];
          const tmp = nextOrder[idx - 1];
          nextOrder[idx - 1] = nextOrder[idx];
          nextOrder[idx] = tmp;
          this._applyOptimisticPatch(roomId, {
            light_priority_order: nextOrder,
            light_order_locked: true,
          });
          this._callService(
            "update_room_config",
            {
              room_id: roomId,
              light_priority_order: nextOrder,
              light_order_locked: true,
            },
            `Moved ${getFriendlyEntityName(this._hass, attr, nextOrder[idx - 1])} up to #${idx}`
          );
        }
      });
    });

    this.shadowRoot.querySelectorAll(".btn-move-light-down").forEach((btn) => {
      btn.addEventListener("click", () => {
        const idx = parseInt(btn.getAttribute("data-idx"), 10);
        if (idx >= 0 && idx < priorityOrder.length - 1) {
          const nextOrder = [...priorityOrder];
          const tmp = nextOrder[idx + 1];
          nextOrder[idx + 1] = nextOrder[idx];
          nextOrder[idx] = tmp;
          this._applyOptimisticPatch(roomId, {
            light_priority_order: nextOrder,
            light_order_locked: true,
          });
          this._callService(
            "update_room_config",
            {
              room_id: roomId,
              light_priority_order: nextOrder,
              light_order_locked: true,
            },
            `Moved ${getFriendlyEntityName(this._hass, attr, nextOrder[idx + 1])} down to #${idx + 2}`
          );
        }
      });
    });

    this.shadowRoot
      .querySelector("#btn-reset-light-order")
      ?.addEventListener("click", () => {
        delete this._optimisticPatches[roomId];
        this._callService(
          "update_room_config",
          {
            room_id: roomId,
            light_order_locked: false,
            light_priority_order: [],
          },
          "Reset to automatic fixture order"
        );
      });

    // Drawer B: Occupancy Hold, Presence Sensors & Lux Placement Roles
    this.shadowRoot
      .querySelector("#sel-vacancy-delay")
      ?.addEventListener("change", (ev) => {
        const secs = parseInt(ev.target.value, 10) || 0;
        this._applyOptimisticPatch(roomId, { vacancy_delay_seconds: secs });
        this._callService(
          "update_room_config",
          { room_id: roomId, vacancy_delay_seconds: secs },
          `Set occupancy hold to ${secs}s`
        );
      });

    this.shadowRoot.querySelectorAll(".chk-toggle-entity").forEach((chk) => {
      chk.addEventListener("change", () => {
        const excluded = [];
        this.shadowRoot.querySelectorAll(".chk-toggle-entity").forEach((c) => {
          if (!c.checked) {
            excluded.push(c.getAttribute("data-eid"));
          }
        });
        this._applyOptimisticPatch(roomId, { excluded_entities: excluded });
        this._callService(
          "update_room_config",
          { room_id: roomId, excluded_entities: excluded },
          "Updated included room devices"
        );
      });
    });

    this.shadowRoot.querySelectorAll(".sel-sensor-placement").forEach((sel) => {
      sel.addEventListener("change", (ev) => {
        const seid = sel.getAttribute("data-seid");
        const placeVal = ev.target.value;
        const nextProfiles = JSON.parse(
          JSON.stringify(attr.sensor_profiles || {})
        );
        nextProfiles[seid] = {
          ...(nextProfiles[seid] || { entity_id: seid }),
          placement: placeVal,
        };
        this._applyOptimisticPatch(roomId, { sensor_profiles: nextProfiles });
        this._callService(
          "update_room_config",
          {
            room_id: roomId,
            sensor_placements: { [seid]: placeVal },
          },
          `Updated sensor role for ${getFriendlyEntityName(this._hass, attr, seid)}`
        );
      });
    });

    // Drawer C: Auto-Saving Room Tuning, Models, AI & Reset
    const saveTuningUpdate = (payload, label) => {
      this._applyOptimisticPatch(roomId, payload);
      this._callService(
        "update_room_config",
        { room_id: roomId, ...payload },
        label
      );
    };

    this.shadowRoot
      .querySelector("#tune-op-mode")
      ?.addEventListener("change", (ev) =>
        saveTuningUpdate(
          { operating_mode: ev.target.value },
          "Updated operating mode"
        )
      );

    this.shadowRoot
      .querySelector("#tune-cct-mode")
      ?.addEventListener("change", (ev) =>
        saveTuningUpdate(
          { cct_mode: ev.target.value },
          "Updated color temperature mode"
        )
      );

    this.shadowRoot
      .querySelector("#tune-cooldown")
      ?.addEventListener("change", (ev) =>
        saveTuningUpdate(
          { on_off_cooldown_minutes: parseFloat(ev.target.value) || 0 },
          "Updated anti-flicker cooldown"
        )
      );

    this.shadowRoot
      .querySelector("#tune-adjust")
      ?.addEventListener("change", (ev) =>
        saveTuningUpdate(
          { adjust_interval_minutes: parseFloat(ev.target.value) || 2 },
          "Updated daylight adjust interval"
        )
      );

    const saveModelSelection = () => {
      const curReg = attr.time_regime || "evening";
      const im =
        this.shadowRoot.querySelector("#tune-illum-model")?.value ||
        "weber_fechner";
      const wm =
        this.shadowRoot.querySelector("#tune-weather-model")?.value ||
        "cave_contrast";
      const sm =
        this.shadowRoot.querySelector("#tune-season-model")?.value ||
        "season_neutral";
      this._callService(
        "update_room_config",
        {
          room_id: roomId,
          regime_models: {
            [curReg]: { illum_model: im, weather_model: wm, season_model: sm },
          },
        },
        `Updated ${curReg} adaptation models`
      );
    };

    this.shadowRoot
      .querySelector("#tune-illum-model")
      ?.addEventListener("change", saveModelSelection);
    this.shadowRoot
      .querySelector("#tune-weather-model")
      ?.addEventListener("change", saveModelSelection);
    this.shadowRoot
      .querySelector("#tune-season-model")
      ?.addEventListener("change", saveModelSelection);

    this.shadowRoot
      .querySelector("#btn-run-ai")
      ?.addEventListener("click", () => {
        this._callService(
          "optimize_with_ai",
          { room_id: roomId },
          `Running AI calibration for ${roomName}...`
        );
      });

    this.shadowRoot
      .querySelector("#btn-reset-this-room")
      ?.addEventListener("click", () => {
        if (
          confirm(
            `Reset ${roomName} thresholds and learned state back to clean defaults?`
          )
        ) {
          delete this._optimisticPatches[roomId];
          this._callService(
            "reset_room",
            { room_id: roomId },
            `${roomName} reset to defaults`
          );
        }
      });
  }

  _bindHubDrawerEvents(outAttr) {
    const saveHubSched = () => {
      const sched = outAttr.regime_schedule || {};
      const regimeSchedule = {
        mode:
          this.shadowRoot.querySelector("#hub-mode")?.value ||
          sched.mode ||
          "hybrid",
        transition_minutes: parseFloat(
          this.shadowRoot.querySelector("#hub-trans-min")?.value || "45"
        ),
        gloom_sky_lux_threshold: parseFloat(
          this.shadowRoot.querySelector("#hub-gloom-lux")?.value || "1500"
        ),
        gloom_early_evening: Boolean(
          this.shadowRoot.querySelector("#hub-gloom-chk")?.checked
        ),
        morning_start_hour: timeStrToDecimalHour(
          this.shadowRoot.querySelector("#hub-morn-time")?.value,
          6.0
        ),
        morning_solar_elev: parseFloat(
          this.shadowRoot.querySelector("#hub-morn-el")?.value || "-4"
        ),
        day_start_hour: timeStrToDecimalHour(
          this.shadowRoot.querySelector("#hub-day-time")?.value,
          10.0
        ),
        day_solar_elev: parseFloat(
          this.shadowRoot.querySelector("#hub-day-el")?.value || "14"
        ),
        evening_start_hour: timeStrToDecimalHour(
          this.shadowRoot.querySelector("#hub-eve-time")?.value,
          17.5
        ),
        evening_solar_elev: parseFloat(
          this.shadowRoot.querySelector("#hub-eve-el")?.value || "9"
        ),
        overnight_start_hour: timeStrToDecimalHour(
          this.shadowRoot.querySelector("#hub-ovn-start")?.value,
          23.0
        ),
        overnight_end_hour: timeStrToDecimalHour(
          this.shadowRoot.querySelector("#hub-ovn-end")?.value,
          6.0
        ),
      };
      this._callService(
        "update_hub_config",
        { regime_schedule: regimeSchedule },
        "Saved global time-of-day schedule"
      );
    };

    this.shadowRoot.querySelectorAll(".auto-hub-sched").forEach((el) => {
      el.addEventListener("change", saveHubSched);
    });

    this.shadowRoot
      .querySelector("#btn-add-area")
      ?.addEventListener("click", () => {
        const areaId = this.shadowRoot.querySelector("#hub-add-area")?.value;
        if (areaId) {
          this._activeTab = areaId;
          this._callService(
            "update_hub_config",
            { add_area_id: areaId },
            `Added room "${areaId}"`
          );
        }
      });

    this.shadowRoot.querySelectorAll(".btn-del-room").forEach((btn) => {
      btn.addEventListener("click", () => {
        const rid = btn.getAttribute("data-room");
        if (rid && confirm(`Remove room "${rid}" from Perceptive Lighting?`)) {
          if (this._activeTab === rid) this._activeTab = null;
          this._callService(
            "update_hub_config",
            { remove_room_id: rid },
            `Removed room "${rid}"`
          );
        }
      });
    });

    this.shadowRoot
      .querySelector("#btn-reset-all")
      ?.addEventListener("click", () => {
        if (confirm("Reset learned thresholds for ALL rooms?")) {
          this._optimisticPatches = {};
          this._callService(
            "reset_room",
            { room_id: "all" },
            "All rooms reset to clean defaults"
          );
        }
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
    background: var(--ha-card-background, var(--card-background-color, #161f30));
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
    font-size: 1.08rem;
    font-weight: 700;
  }
  .subtitle {
    font-size: 0.78rem;
    color: var(--secondary-text-color);
    margin-top: 2px;
  }
  .pill-badge {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    padding: 4px 10px;
    border-radius: 999px;
    font-size: 0.74rem;
    font-weight: 600;
    cursor: pointer;
    border: 1px solid rgba(148, 163, 184, 0.25);
    background: rgba(148, 163, 184, 0.1);
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
    border: 1px solid rgba(59, 130, 246, 0.25);
    font-size: 0.77rem;
    line-height: 1.4;
    margin: 8px 0;
  }
  .tune-row {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin-top: 10px;
  }
  .btn-action {
    padding: 7px 11px;
    border-radius: 9px;
    font-size: 0.77rem;
    font-weight: 600;
    cursor: pointer;
    border: 1px solid rgba(148, 163, 184, 0.25);
    background: rgba(148, 163, 184, 0.1);
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

    this.shadowRoot.innerHTML = `
      <style>${CARD_STYLES}</style>
      <ha-card>
        <div class="header-row">
          <div>
            <div class="room-title">${escapeHtml(roomName)}</div>
            <div class="subtitle">
              ${escapeHtml((attr.time_regime || "day").toUpperCase())} • <b>${attr.effective_spatial_lux ?? attr.raw_lux} lx</b> (Turn-On ≤ ${attr.turn_on_lux_threshold} lx) • Target: <b>${attr.recommended_brightness_pct}%</b>
            </div>
          </div>
          <div style="display:flex;gap:6px;align-items:center;">
            <div class="pill-badge ${isLocked ? "locked" : "auto"}" id="btn-toggle-lock">
              ${isLocked ? "🔒 Locked" : `✨ ${isOccupied ? "Occupied" : "Vacant"}`}
            </div>
            <a class="pill-badge" href="/perceptive-lighting">⚙️ Studio</a>
          </div>
        </div>

        <div class="reason-box">
          ${escapeHtml(attr.decision_reason || "Evaluating room lighting...")}
        </div>

        <div class="tune-row">
          <button class="btn-action" id="btn-nudge-darker">🔉 Dimmer (-)</button>
          <button class="btn-action" id="btn-nudge-lighter">🔆 Brighter (+)</button>
          <button class="btn-action" id="btn-feels-dark-now">🌑 Should Be On</button>
          <button class="btn-action" id="btn-feels-bright-now">☀️ Bright Enough</button>
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
    return { title: "Perceptive Lighting — Home" };
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
          <div style="padding:10px 12px;border-radius:12px;background:rgba(148,163,184,0.08);margin-bottom:8px;display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap;">
            <div>
              <div style="font-weight:600;font-size:0.93rem;">
                ${escapeHtml(rname)}
                <span style="font-size:0.75rem;font-weight:400;color:var(--secondary-text-color);">
                  (${isOcc ? "Occupied" : "Vacant"} • ${a.effective_spatial_lux ?? a.raw_lux} lx)
                </span>
              </div>
              <div style="font-size:0.76rem;color:var(--secondary-text-color);margin-top:2px;">
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
