/**
 * Perceptive Lighting v2.0.2 — Modern Web & Mobile Studio UI + Lovelace Cards
 *
 * Design Principles:
 * - Progressive Disclosure: Clean segmented sub-navigation (Scenes & Time of Day,
 *   Light Shedding Order, Sensors & Devices, AI & Tuning) so only one focused
 *   workflow is on screen at a time.
 * - Single-Regime Focus Selector: Tap Morning, Day, Evening, or Overnight to view
 *   and edit that regime's scene, off-state override, and thresholds without a
 *   wall of 36 inputs.
 * - Presence-Only OR Entity-Based Scene Rules: Create rules triggered purely by
 *   room presence/occupancy OR by a specific entity state (e.g. TV playing).
 * - Instant 0ms Optimistic UI Updates: Reordering lights (Shed Earlier / Keep Longer),
 *   toggling locks, adding/removing rules, and toggling entities update the UI
 *   immediately without waiting for a page refresh.
 */

const DOMAIN = "perceptive_lighting";

const REGIME_META = {
  morning: { label: "Morning", icon: "🌅", short: "Morn" },
  day: { label: "Day", icon: "☀️", short: "Day" },
  evening: { label: "Evening", icon: "🌆", short: "Eve" },
  overnight: { label: "Overnight", icon: "🌙", short: "Night" },
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
 * 1. MODERN FULL-SCREEN SIDEBAR STUDIO: <perceptive-lighting-panel>
 * ========================================================================== */

const PANEL_STYLES = `
  :host {
    display: block;
    min-height: 100vh;
    background: var(--primary-background-color, #0b0f19);
    color: var(--primary-text-color, #f8fafc);
    font-family: var(--paper-font-body1_-_font-family, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif);
    box-sizing: border-box;
    -webkit-font-smoothing: antialiased;
  }
  .app-shell {
    max-width: 980px;
    margin: 0 auto;
    padding: 16px 16px 64px 16px;
  }
  /* Top App Bar */
  .app-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    margin-bottom: 14px;
    flex-wrap: wrap;
  }
  .header-left {
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
    border-radius: 11px;
    border: 1px solid rgba(148, 163, 184, 0.2);
    background: var(--ha-card-background, var(--card-background-color, #1e293b));
    color: var(--primary-text-color);
    font-size: 1.05rem;
    cursor: pointer;
    transition: all 0.15s ease;
  }
  .icon-btn:hover {
    background: rgba(148, 163, 184, 0.18);
  }
  .app-title {
    font-size: 1.22rem;
    font-weight: 700;
    letter-spacing: -0.01em;
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .sky-pill {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 5px 11px;
    border-radius: 999px;
    font-size: 0.78rem;
    font-weight: 600;
    background: rgba(56, 189, 248, 0.12);
    color: #7dd3fc;
    border: 1px solid rgba(56, 189, 248, 0.25);
  }
  /* Room Selector Bar */
  .room-pills {
    display: flex;
    align-items: center;
    gap: 8px;
    overflow-x: auto;
    padding-bottom: 6px;
    margin-bottom: 14px;
    scrollbar-width: none;
  }
  .room-pills::-webkit-scrollbar {
    display: none;
  }
  .room-pill {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    padding: 9px 15px;
    border-radius: 999px;
    font-size: 0.86rem;
    font-weight: 600;
    cursor: pointer;
    white-space: nowrap;
    border: 1px solid rgba(148, 163, 184, 0.2);
    background: var(--ha-card-background, var(--card-background-color, #1e293b));
    color: var(--secondary-text-color, #94a3b8);
    transition: all 0.16s ease;
  }
  .room-pill:hover {
    color: var(--primary-text-color);
    border-color: rgba(148, 163, 184, 0.4);
  }
  .room-pill.active {
    background: var(--primary-color, #3b82f6);
    color: #ffffff;
    border-color: var(--primary-color, #3b82f6);
    box-shadow: 0 4px 12px rgba(59, 130, 246, 0.3);
  }
  .status-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
  }
  .status-dot.occ {
    background: #10b981;
    box-shadow: 0 0 6px #10b981;
  }
  .status-dot.vac {
    background: #64748b;
  }
  .room-pill.active .status-dot.occ {
    background: #a7f3d0;
  }
  /* Hero Card */
  .hero-card {
    background: var(--ha-card-background, var(--card-background-color, #1e293b));
    border: 1px solid rgba(148, 163, 184, 0.18);
    border-radius: 20px;
    padding: 18px 20px;
    box-shadow: 0 4px 20px rgba(0, 0, 0, 0.12);
    margin-bottom: 14px;
  }
  .hero-top {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 14px;
    flex-wrap: wrap;
  }
  .hero-room-name {
    font-size: 1.35rem;
    font-weight: 800;
    letter-spacing: -0.02em;
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .why-pill {
    margin-top: 6px;
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 5px 11px;
    border-radius: 10px;
    background: rgba(59, 130, 246, 0.11);
    border: 1px solid rgba(59, 130, 246, 0.25);
    font-size: 0.81rem;
    color: var(--primary-text-color);
    line-height: 1.4;
  }
  /* Tactile Stepper Pill */
  .stepper-group {
    display: inline-flex;
    align-items: center;
    background: rgba(15, 23, 42, 0.55);
    border: 1px solid rgba(148, 163, 184, 0.22);
    border-radius: 14px;
    padding: 4px;
    gap: 4px;
  }
  .stepper-btn {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 9px 15px;
    border-radius: 10px;
    border: none;
    background: var(--primary-color, #3b82f6);
    color: #fff;
    font-size: 0.85rem;
    font-weight: 700;
    cursor: pointer;
    transition: transform 0.1s ease, filter 0.15s ease;
  }
  .stepper-btn:hover {
    filter: brightness(1.1);
  }
  .stepper-btn:active {
    transform: scale(0.96);
  }
  .stepper-btn.dimmer {
    background: rgba(148, 163, 184, 0.18);
    color: var(--primary-text-color);
  }
  .hero-kpis {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
    gap: 10px;
    margin-top: 16px;
    padding-top: 14px;
    border-top: 1px solid rgba(148, 163, 184, 0.14);
  }
  .kpi-item {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }
  .kpi-label {
    font-size: 0.72rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--secondary-text-color, #94a3b8);
  }
  .kpi-value {
    font-size: 1.15rem;
    font-weight: 800;
  }
  .kpi-sub {
    font-size: 0.75rem;
    color: var(--secondary-text-color, #94a3b8);
  }
  .quick-chips {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-top: 14px;
  }
  .chip-btn {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 7px 12px;
    border-radius: 999px;
    font-size: 0.78rem;
    font-weight: 600;
    cursor: pointer;
    border: 1px solid rgba(148, 163, 184, 0.22);
    background: rgba(148, 163, 184, 0.08);
    color: var(--primary-text-color);
    transition: all 0.15s ease;
  }
  .chip-btn:hover {
    background: rgba(148, 163, 184, 0.18);
  }
  .chip-btn.active-lock {
    background: rgba(239, 68, 68, 0.18);
    color: #f87171;
    border-color: rgba(239, 68, 68, 0.4);
  }
  /* Segmented Sub-Navigation */
  .subnav {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 6px;
    padding: 5px;
    border-radius: 14px;
    background: var(--ha-card-background, var(--card-background-color, #1e293b));
    border: 1px solid rgba(148, 163, 184, 0.18);
    margin-bottom: 14px;
  }
  @media (max-width: 640px) {
    .subnav {
      grid-template-columns: repeat(2, 1fr);
    }
  }
  .subnav-btn {
    padding: 9px 10px;
    border-radius: 10px;
    border: none;
    background: transparent;
    color: var(--secondary-text-color, #94a3b8);
    font-size: 0.83rem;
    font-weight: 600;
    cursor: pointer;
    text-align: center;
    transition: all 0.15s ease;
  }
  .subnav-btn:hover {
    color: var(--primary-text-color);
  }
  .subnav-btn.active {
    background: rgba(59, 130, 246, 0.18);
    color: #60a5fa;
    font-weight: 700;
  }
  /* Content Surface Card */
  .surface-card {
    background: var(--ha-card-background, var(--card-background-color, #1e293b));
    border: 1px solid rgba(148, 163, 184, 0.18);
    border-radius: 18px;
    padding: 18px 20px;
    margin-bottom: 14px;
  }
  .card-title-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    margin-bottom: 6px;
    flex-wrap: wrap;
  }
  .card-title {
    font-size: 1.02rem;
    font-weight: 700;
  }
  .card-subtitle {
    font-size: 0.81rem;
    color: var(--secondary-text-color, #94a3b8);
    line-height: 1.45;
    margin-bottom: 16px;
  }
  /* Time-of-Day Segmented Selector */
  .regime-tabs {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 8px;
    margin-bottom: 18px;
  }
  @media (max-width: 560px) {
    .regime-tabs {
      grid-template-columns: repeat(2, 1fr);
    }
  }
  .regime-tab {
    padding: 10px 12px;
    border-radius: 12px;
    border: 1px solid rgba(148, 163, 184, 0.2);
    background: rgba(148, 163, 184, 0.06);
    color: var(--primary-text-color);
    cursor: pointer;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 3px;
    transition: all 0.15s ease;
  }
  .regime-tab.selected {
    border-color: var(--primary-color, #3b82f6);
    background: rgba(59, 130, 246, 0.16);
    box-shadow: 0 0 0 1px var(--primary-color, #3b82f6);
  }
  .regime-tab-title {
    font-size: 0.88rem;
    font-weight: 700;
  }
  .regime-tab-sub {
    font-size: 0.72rem;
    color: var(--secondary-text-color, #94a3b8);
  }
  /* Clean Form Controls */
  .form-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
    gap: 16px;
  }
  .field-box {
    background: rgba(148, 163, 184, 0.06);
    border: 1px solid rgba(148, 163, 184, 0.15);
    border-radius: 14px;
    padding: 14px;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .field-label {
    font-size: 0.8rem;
    font-weight: 700;
    color: var(--primary-text-color);
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  .field-hint {
    font-size: 0.74rem;
    color: var(--secondary-text-color, #94a3b8);
    line-height: 1.35;
  }
  select,
  input[type="text"],
  input[type="number"] {
    width: 100%;
    box-sizing: border-box;
    padding: 9px 11px;
    border-radius: 10px;
    border: 1px solid rgba(148, 163, 184, 0.28);
    background: var(--primary-background-color, #0f172a);
    color: var(--primary-text-color, #f8fafc);
    font-size: 0.85rem;
  }
  input[type="range"] {
    width: 100%;
    accent-color: var(--primary-color, #3b82f6);
    cursor: pointer;
  }
  .btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 6px;
    padding: 9px 15px;
    border-radius: 10px;
    font-size: 0.83rem;
    font-weight: 600;
    cursor: pointer;
    border: 1px solid rgba(148, 163, 184, 0.25);
    background: rgba(148, 163, 184, 0.1);
    color: var(--primary-text-color);
    transition: all 0.15s ease;
  }
  .btn:hover {
    background: rgba(148, 163, 184, 0.2);
  }
  .btn.primary {
    background: var(--primary-color, #3b82f6);
    color: #fff;
    border-color: var(--primary-color, #3b82f6);
  }
  .btn.danger {
    background: rgba(239, 68, 68, 0.15);
    color: #f87171;
    border-color: rgba(239, 68, 68, 0.35);
  }
  /* Light Priority Row */
  .light-item {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    padding: 12px 14px;
    border-radius: 14px;
    background: rgba(148, 163, 184, 0.06);
    border: 1px solid rgba(148, 163, 184, 0.16);
    margin-bottom: 8px;
    transition: all 0.15s ease;
  }
  .light-item.is-last {
    border-color: rgba(99, 102, 241, 0.45);
    background: rgba(99, 102, 241, 0.09);
  }
  .light-rank {
    width: 32px;
    height: 32px;
    border-radius: 9px;
    background: rgba(148, 163, 184, 0.15);
    display: flex;
    align-items: center;
    justify-content: center;
    font-weight: 800;
    font-size: 0.84rem;
    flex-shrink: 0;
  }
  .arrow-btn {
    width: 36px;
    height: 36px;
    border-radius: 10px;
    border: 1px solid rgba(148, 163, 184, 0.25);
    background: rgba(148, 163, 184, 0.1);
    color: var(--primary-text-color);
    font-size: 0.95rem;
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    justify-content: center;
  }
  .arrow-btn:hover:not(:disabled) {
    background: var(--primary-color, #3b82f6);
    color: #fff;
    border-color: var(--primary-color, #3b82f6);
  }
  .arrow-btn:disabled {
    opacity: 0.3;
    cursor: default;
  }
  .rule-item {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    padding: 12px 14px;
    border-radius: 13px;
    background: rgba(148, 163, 184, 0.07);
    border: 1px solid rgba(148, 163, 184, 0.18);
    margin-bottom: 8px;
    flex-wrap: wrap;
  }
  details.adv-accordion {
    margin-top: 14px;
    border-radius: 12px;
    border: 1px solid rgba(148, 163, 184, 0.18);
    background: rgba(148, 163, 184, 0.04);
    padding: 10px 14px;
  }
  details.adv-accordion summary {
    cursor: pointer;
    font-size: 0.82rem;
    font-weight: 600;
    color: var(--secondary-text-color, #94a3b8);
    user-select: none;
  }
  .toast {
    position: fixed;
    bottom: 22px;
    right: 22px;
    padding: 11px 18px;
    border-radius: 12px;
    background: #10b981;
    color: #ffffff;
    font-weight: 700;
    font-size: 0.85rem;
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.35);
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
    this._roomSubTab = "scenes"; // "scenes" | "lights" | "sensors" | "ai"
    this._selectedRegime = null; // "morning" | "day" | "evening" | "overnight"
    this._showAddRuleForm = false;
    this._newRuleTriggerType = "presence_only"; // "presence_only" | "entity"
    // Optimistic client state so UI updates in 0ms before HA websocket round-trip
    this._optimisticPatches = {};
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

    // Only skip background re-renders when user is typing in a text/number input or open select
    const activeEl = this.shadowRoot ? this.shadowRoot.activeElement : null;
    if (
      activeEl &&
      ((activeEl.tagName === "INPUT" &&
        (activeEl.type === "text" ||
          activeEl.type === "number" ||
          activeEl.type === "range")) ||
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
      this._roomSubTab,
      this._selectedRegime,
      out ? `${out.state}:${JSON.stringify(outAttr.regime_weights)}:${JSON.stringify(outAttr.regime_schedule)}` : "no_out",
    ];
    for (const r of rooms) {
      const a = r.attributes;
      parts.push(
        [
          a.room_id,
          r.state,
          a.raw_lux,
          a.is_occupied,
          a.automation_locked,
          a.manual_override_active,
          a.decision_reason,
          a.recommended_brightness_pct,
          a.active_shed_count,
          a.light_order_locked,
          JSON.stringify(a.light_priority_order),
          JSON.stringify(a.regime_configs),
          JSON.stringify(a.scene_triggers),
          JSON.stringify(a.excluded_entities),
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
    this._render();
  }

  _showToast(msg) {
    this._toastMsg = msg;
    if (this._toastTimer) clearTimeout(this._toastTimer);
    this._render();
    this._toastTimer = setTimeout(() => {
      this._toastMsg = null;
      this._render();
    }, 2600);
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
      this._activeTab =
        rooms.length > 0 ? rooms[0].attributes.room_id : "_hub_settings";
    }

    const outAttr = outdoor ? outdoor.attributes : {};
    const outLux = outdoor ? Math.round(Number(outdoor.state) || 0) : 0;
    const curOutdoorRegime = outAttr.time_regime || "evening";
    if (!this._selectedRegime) {
      this._selectedRegime = curOutdoorRegime;
    }

    const roomPillsHtml = [
      ...rooms.map((st) => {
        const a = this._getEffectiveRoomAttr(st);
        const rid = a.room_id;
        const rname = a.room_name || rid;
        const isOcc = Boolean(a.is_occupied);
        const isAct = this._activeTab === rid;
        return `
          <button class="room-pill ${isAct ? "active" : ""}" data-tab="${escapeHtml(rid)}">
            <span class="status-dot ${isOcc ? "occ" : "vac"}"></span>
            <span>${escapeHtml(rname)}</span>
            <span style="font-size:0.74rem;opacity:0.8;">${a.raw_lux} lx</span>
          </button>
        `;
      }),
      `<button class="room-pill ${
        this._activeTab === "_hub_settings" ? "active" : ""
      }" data-tab="_hub_settings">
        🌍 Schedule & Hub
      </button>`,
    ].join("");

    let contentHtml = "";
    if (this._activeTab === "_hub_settings") {
      contentHtml = this._renderHubView(outAttr, rooms);
    } else {
      const activeSensor =
        rooms.find((r) => r.attributes.room_id === this._activeTab) || rooms[0];
      if (activeSensor) {
        contentHtml = this._renderRoomView(activeSensor, outAttr);
      } else {
        contentHtml = `<div class="surface-card">No rooms configured yet. Open <b>Schedule & Hub</b> to add an Area.</div>`;
      }
    }

    const activeRegimeMeta = REGIME_META[curOutdoorRegime] || REGIME_META.day;

    this.shadowRoot.innerHTML = `
      <style>${PANEL_STYLES}</style>
      <div class="app-shell">
        <div class="app-header">
          <div class="header-left">
            <button class="icon-btn" id="btn-ha-menu" title="Toggle Sidebar">☰</button>
            <div class="app-title">
              <span>🔆 Perceptive Lighting</span>
            </div>
          </div>
          <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
            <span class="sky-pill" title="${escapeHtml(outAttr.regime_explanation || "")}">
              ${activeRegimeMeta.icon} ${activeRegimeMeta.label} • Sky ${outLux} lx (${escapeHtml(outAttr.weather_condition || "clear")})
            </span>
          </div>
        </div>

        <div class="room-pills">
          ${roomPillsHtml}
        </div>

        ${contentHtml}
      </div>
      ${this._toastMsg ? `<div class="toast">✓ ${escapeHtml(this._toastMsg)}</div>` : ""}
    `;

    this._bindShellEvents();
    if (this._activeTab === "_hub_settings") {
      this._bindHubEvents(outAttr);
    } else {
      const activeSensor =
        rooms.find((r) => r.attributes.room_id === this._activeTab) || rooms[0];
      if (activeSensor) {
        this._bindRoomEvents(activeSensor);
      }
    }
  }

  _renderRoomView(sensor, outAttr) {
    const attr = this._getEffectiveRoomAttr(sensor);
    const roomId = attr.room_id;
    const roomName = attr.room_name || roomId;
    const isLocked = Boolean(attr.automation_locked);
    const isOccupied = Boolean(attr.is_occupied);
    const totalLights = (attr.included_lights || []).length;
    const shedCount = Number(attr.active_shed_count || 0);
    const activeLightsCount = Math.max(0, totalLights - shedCount);

    let subViewHtml = "";
    if (this._roomSubTab === "scenes") {
      subViewHtml = this._renderScenesAndRegimesSubTab(attr, outAttr);
    } else if (this._roomSubTab === "lights") {
      subViewHtml = this._renderLightsOrderSubTab(attr);
    } else if (this._roomSubTab === "sensors") {
      subViewHtml = this._renderSensorsAndEntitiesSubTab(attr);
    } else {
      subViewHtml = this._renderAiAndTuningSubTab(attr);
    }

    return `
      <!-- COMPACT HERO STATUS & DIRECT CONTROL -->
      <div class="hero-card">
        <div class="hero-top">
          <div>
            <div class="hero-room-name">
              <span>${escapeHtml(roomName)}</span>
              <span class="status-dot ${isOccupied ? "occ" : "vac"}" title="${
                isOccupied ? "Occupied" : "Vacant"
              }"></span>
            </div>
            <div class="why-pill">
              <span>💬 ${escapeHtml(attr.decision_reason || "Evaluating room lighting...")}</span>
            </div>
          </div>

          <div class="stepper-group">
            <button class="stepper-btn dimmer" id="btn-hero-dimmer" title="Step dimmer down to 1%, then turn off one light source at a time">
              🔉 Dimmer (-)
            </button>
            <button class="stepper-btn" id="btn-hero-brighter" title="Restore shed light or step brightness up (+10%)">
              🔆 Brighter (+)
            </button>
          </div>
        </div>

        <div class="hero-kpis">
          <div class="kpi-item">
            <span class="kpi-label">Measured Room Lux</span>
            <span class="kpi-value">${attr.raw_lux} lx</span>
            <span class="kpi-sub">Turn-On ≤ ${attr.turn_on_lux_threshold} lx</span>
          </div>
          <div class="kpi-item">
            <span class="kpi-label">Target Brightness</span>
            <span class="kpi-value">${attr.recommended_brightness_pct}%</span>
            <span class="kpi-sub">1% minimum floor</span>
          </div>
          <div class="kpi-item">
            <span class="kpi-label">Active Fixtures</span>
            <span class="kpi-value">${activeLightsCount} / ${totalLights}</span>
            <span class="kpi-sub">${shedCount > 0 ? `${shedCount} shed at 1% floor` : "All fixtures enabled"}</span>
          </div>
          <div class="kpi-item">
            <span class="kpi-label">Active Scene</span>
            <span class="kpi-value" style="font-size:0.96rem;">${escapeHtml(
              (attr.available_scenes && attr.available_scenes[attr.resolved_scene_id]) ||
                attr.resolved_scene_id ||
                "All Lights"
            )}</span>
            <span class="kpi-sub">${escapeHtml(attr.resolved_scene_reason || "Default")}</span>
          </div>
        </div>

        <div class="quick-chips">
          <button class="chip-btn" id="btn-chip-dark">🌑 Feels Too Dark Now (Turn On)</button>
          <button class="chip-btn" id="btn-chip-bright">☀️ Feels Bright Enough (Turn Off)</button>
          <button class="chip-btn" id="btn-chip-capture">📸 Save Current Lights as Scene</button>
          <button class="chip-btn ${isLocked ? "active-lock" : ""}" id="btn-chip-lock">
            ${isLocked ? "🔒 Automation Locked (Tap to Unlock)" : "✨ Auto Control On"}
          </button>
        </div>
      </div>

      <!-- SEGMENTED SUB-NAVIGATION (PROGRESSIVE DISCLOSURE) -->
      <div class="subnav">
        <button class="subnav-btn ${this._roomSubTab === "scenes" ? "active" : ""}" data-subtab="scenes">
          🎬 Time of Day & Rules
        </button>
        <button class="subnav-btn ${this._roomSubTab === "lights" ? "active" : ""}" data-subtab="lights">
          💡 Light Shedding (${totalLights})
        </button>
        <button class="subnav-btn ${this._roomSubTab === "sensors" ? "active" : ""}" data-subtab="sensors">
          📡 Sensors & Devices
        </button>
        <button class="subnav-btn ${this._roomSubTab === "ai" ? "active" : ""}" data-subtab="ai">
          🧠 AI & Tuning
        </button>
      </div>

      ${subViewHtml}
    `;
  }

  _buildSceneOptionsHtml(scenesMap, selectedVal, includeOffOption = false) {
    const baseOpts = includeOffOption
      ? [["off", "⏹️ Turn All Lights Off"]]
      : [["all_lights", "💡 All Room Lights (Dynamic)"]];

    for (const [sid, sname] of Object.entries(scenesMap || {})) {
      if (sid !== "default") {
        baseOpts.push([sid, sname]);
      }
    }
    if (this._hass && this._hass.states) {
      Object.keys(this._hass.states)
        .filter((eid) => eid.startsWith("scene.") && !(scenesMap && scenesMap[eid]))
        .sort()
        .forEach((eid) => {
          const st = this._hass.states[eid];
          const fn = (st && st.attributes && st.attributes.friendly_name) || eid;
          baseOpts.push([eid, `Scene: ${fn}`]);
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
  }

  _renderScenesAndRegimesSubTab(attr, outAttr) {
    const scenesMap = attr.available_scenes || {};
    const regimeWeights = attr.regime_weights || outAttr.regime_weights || {};
    const regimeConfigs = attr.regime_configs || {};
    const rkey = this._selectedRegime || attr.time_regime || "evening";
    const meta = REGIME_META[rkey] || REGIME_META.evening;
    const rcfg = regimeConfigs[rkey] || {};

    const occScene = rcfg.occupied_scene || "all_lights";
    const offScene = rcfg.off_scene || "off";
    const offWhenUnocc = rcfg.off_scene_when_unoccupied !== false;
    const offWhenBright = Boolean(rcfg.off_scene_when_bright);
    const turnOnLux = Number(rcfg.turn_on_lux ?? 10.0);
    const targetBr = Number(rcfg.target_brightness_pct ?? 60);
    const targetTotLux = Number(rcfg.target_total_lux ?? 12.0);
    const defShed = Number(rcfg.shed_count ?? 0);
    const totalLights = (attr.included_lights || []).length;

    const lwModels = (attr.learned_weights && attr.learned_weights.regime_models) || {};
    const mSpec = lwModels[rkey] || {};
    const curIllumModel = mSpec.illum_model || "weber_fechner";
    const curWeatherModel = mSpec.weather_model || "cave_contrast";
    const curSeasonModel = mSpec.season_model || "season_neutral";

    const regimeTabsHtml = Object.entries(REGIME_META)
      .map(([k, m]) => {
        const wPct = Math.round((Number(regimeWeights[k]) || 0) * 100);
        const isSel = k === rkey;
        return `
          <button class="regime-tab ${isSel ? "selected" : ""}" data-select-regime="${k}">
            <span class="regime-tab-title">${m.icon} ${m.label}</span>
            <span class="regime-tab-sub">${
              wPct > 0 ? `🟢 ${wPct}% Active Now` : "Tap to configure"
            }</span>
          </button>
        `;
      })
      .join("");

    // Scene Triggers (Presence-Only or Entity-Based)
    const triggers = attr.scene_triggers || [];
    const rulesHtml = triggers
      .map((t, idx) => {
        const regLbl =
          t.regime && REGIME_META[t.regime]
            ? `${REGIME_META[t.regime].icon} ${REGIME_META[t.regime].label}`
            : "🕒 Any Time of Day";
        const isPresenceOnly =
          !t.entity_id || t.entity_id === "presence_only";
        const condDesc = isPresenceOnly
          ? `🚶 <b>Presence Only</b> (Whenever Room is Occupied)`
          : `⚡ When <code>${escapeHtml(t.entity_id)}</code> is <b>${escapeHtml(
              Array.isArray(t.states) ? t.states.join(", ") : t.states || "on, playing"
            )}</b>`;
        const sceneLbl = scenesMap[t.scene_id] || t.scene_id;
        const dynLbl =
          t.scale_dynamically !== false ? "Adapts with Daylight" : "Fixed Brightness";

        return `
          <div class="rule-item">
            <div>
              <div style="font-size:0.86rem;font-weight:600;">
                ${regLbl} • ${condDesc}
              </div>
              <div style="font-size:0.78rem;color:var(--secondary-text-color);margin-top:3px;">
                ➔ Apply <b>${escapeHtml(sceneLbl)}</b> (${dynLbl})
              </div>
            </div>
            <button class="btn danger btn-del-rule" data-idx="${idx}" style="padding:6px 11px;">Remove</button>
          </div>
        `;
      })
      .join("");

    // Candidate entities for optional entity triggers
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
      <!-- FOCUSED SINGLE-REGIME EDITOR -->
      <div class="surface-card">
        <div class="card-title-row">
          <span class="card-title">🕒 Time-of-Day Behavior</span>
          <button class="btn primary" id="btn-save-active-regime">
            💾 Save ${meta.label} Settings
          </button>
        </div>
        <div class="card-subtitle">
          Select a time of day below to configure its default scene, brightness, turn-on lux threshold, and off-state behavior.
        </div>

        <div class="regime-tabs">
          ${regimeTabsHtml}
        </div>

        <div class="form-grid">
          <div class="field-box">
            <div class="field-label">
              <span>🚶 When Occupied (${meta.label})</span>
            </div>
            <div class="field-hint">Default scene when presence is detected in ${meta.label} (no extra trigger required).</div>
            <select id="reg-occ-scene">
              ${this._buildSceneOptionsHtml(scenesMap, occScene, false)}
            </select>
          </div>

          <div class="field-box">
            <div class="field-label">
              <span>⏹️ When Vacant / Bright Enough</span>
            </div>
            <div class="field-hint">Turn lights off, or switch to a background scene (Scene B) instead of off.</div>
            <select id="reg-off-scene">
              ${this._buildSceneOptionsHtml(scenesMap, offScene, true)}
            </select>
            <div style="display:flex;gap:12px;flex-wrap:wrap;margin-top:4px;font-size:0.77rem;">
              <label style="display:flex;align-items:center;gap:5px;cursor:pointer;">
                <input type="checkbox" id="reg-off-unocc" ${offWhenUnocc ? "checked" : ""} />
                <span>Use when Unoccupied</span>
              </label>
              <label style="display:flex;align-items:center;gap:5px;cursor:pointer;">
                <input type="checkbox" id="reg-off-bright" ${offWhenBright ? "checked" : ""} />
                <span>Use when Bright Enough</span>
              </label>
            </div>
          </div>

          <div class="field-box">
            <div class="field-label">
              <span>🔆 Target Brightness</span>
              <span id="lbl-reg-br" style="color:#60a5fa;">${targetBr}%</span>
            </div>
            <div class="field-hint">Default light brightness for ${meta.label} (supports 1% minimum floor).</div>
            <input type="range" id="reg-target-br" min="1" max="100" step="1" value="${targetBr}" />
          </div>

          <div class="field-box">
            <div class="field-label">
              <span>🌑 Turn-On Lux Threshold</span>
              <span style="color:#60a5fa;">≤ ${turnOnLux} lx</span>
            </div>
            <div class="field-hint">Lights turn on when room lux is at or below this level (currently ${attr.raw_lux} lx).</div>
            <input type="number" id="reg-turn-on-lux" min="0.5" max="250" step="0.5" value="${turnOnLux}" />
          </div>
        </div>

        <details class="adv-accordion">
          <summary>⚙️ Advanced ${meta.label} Math Models & Target Lux</summary>
          <div class="form-grid" style="margin-top:12px;">
            <div class="field-box">
              <div class="field-label"><span>Target Total Room Lux (lx)</span></div>
              <input type="number" id="reg-target-tot-lux" min="1" max="300" step="0.5" value="${targetTotLux}" />
            </div>
            <div class="field-box">
              <div class="field-label"><span>Default Shed Lights Count</span></div>
              <input type="number" id="reg-shed-count" min="0" max="${Math.max(0, totalLights - 1)}" step="1" value="${defShed}" />
            </div>
            <div class="field-box">
              <div class="field-label"><span>Illuminance Curve</span></div>
              <select id="reg-illum-model">
                ${Object.entries(ILLUM_MODEL_OPTIONS)
                  .map(
                    ([k, lbl]) =>
                      `<option value="${k}" ${k === curIllumModel ? "selected" : ""}>${lbl}</option>`
                  )
                  .join("")}
              </select>
            </div>
            <div class="field-box">
              <div class="field-label"><span>Weather & Season Models</span></div>
              <select id="reg-weather-model" style="margin-bottom:6px;">
                ${Object.entries(WEATHER_MODEL_OPTIONS)
                  .map(
                    ([k, lbl]) =>
                      `<option value="${k}" ${k === curWeatherModel ? "selected" : ""}>${lbl}</option>`
                  )
                  .join("")}
              </select>
              <select id="reg-season-model">
                ${Object.entries(SEASON_MODEL_OPTIONS)
                  .map(
                    ([k, lbl]) =>
                      `<option value="${k}" ${k === curSeasonModel ? "selected" : ""}>${lbl}</option>`
                  )
                  .join("")}
              </select>
            </div>
          </div>
        </details>
      </div>

      <!-- SCENE RULES & TRIGGERS (PRESENCE-ONLY OR SPECIFIC ENTITY) -->
      <div class="surface-card">
        <div class="card-title-row">
          <span class="card-title">⚡ Scene Rules & Conditional Triggers</span>
          <button class="btn primary" id="btn-toggle-add-rule">
            ${this._showAddRuleForm ? "✕ Close" : "➕ Add Scene Rule"}
          </button>
        </div>
        <div class="card-subtitle">
          Create rules that apply a scene during a time of day based on <b>Presence Only</b> (no extra entity required) or when a <b>Specific Device</b> (like a TV or Media Player) is on/playing.
        </div>

        ${
          this._showAddRuleForm
            ? `
          <div class="field-box" style="margin-bottom:14px;border-color:rgba(59,130,246,0.4);">
            <div class="form-grid">
              <div>
                <div class="field-label" style="margin-bottom:4px;">1. Time of Day</div>
                <select id="rule-regime">
                  <option value="${rkey}">${meta.icon} ${meta.label} Only</option>
                  <option value="any">🕒 Any Time of Day</option>
                  <option value="morning">🌅 Morning Only</option>
                  <option value="day">☀️ Day Only</option>
                  <option value="evening">🌆 Evening Only</option>
                  <option value="overnight">🌙 Overnight Only</option>
                </select>
              </div>
              <div>
                <div class="field-label" style="margin-bottom:4px;">2. Trigger Condition</div>
                <select id="rule-trigger-type">
                  <option value="presence_only" ${
                    this._newRuleTriggerType === "presence_only" ? "selected" : ""
                  }>🚶 Presence Only (Whenever Room is Occupied)</option>
                  <option value="entity" ${
                    this._newRuleTriggerType === "entity" ? "selected" : ""
                  }>⚡ When a Specific Device / Entity is Active (e.g. TV)</option>
                </select>
              </div>
              ${
                this._newRuleTriggerType === "entity"
                  ? `
                <div>
                  <div class="field-label" style="margin-bottom:4px;">Trigger Entity</div>
                  <select id="rule-entity">
                    ${candidateTriggerEntities
                      .map((eid) => `<option value="${escapeHtml(eid)}">${escapeHtml(eid)}</option>`)
                      .join("")}
                  </select>
                </div>
                <div>
                  <div class="field-label" style="margin-bottom:4px;">Active State(s)</div>
                  <input type="text" id="rule-states" value="playing, on" />
                </div>
              `
                  : ""
              }
              <div>
                <div class="field-label" style="margin-bottom:4px;">3. Scene to Apply</div>
                <select id="rule-scene">
                  ${this._buildSceneOptionsHtml(scenesMap, "all_lights", false)}
                </select>
              </div>
            </div>
            <div style="display:flex;justify-content:space-between;align-items:center;margin-top:12px;flex-wrap:wrap;gap:10px;">
              <label style="display:flex;align-items:center;gap:6px;font-size:0.8rem;cursor:pointer;">
                <input type="checkbox" id="rule-dynamic" checked />
                <span>Automatically brighten & dim scene with real-world daylight</span>
              </label>
              <button class="btn primary" id="btn-confirm-add-rule">✓ Save Rule</button>
            </div>
          </div>
        `
            : ""
        }

        <div>
          ${
            rulesHtml ||
            `<div style="font-size:0.83rem;color:var(--secondary-text-color);">No custom scene rules added yet. Each time of day is using its default Occupied Scene above.</div>`
          }
        </div>
      </div>
    `;
  }

  _renderLightsOrderSubTab(attr) {
    const priorityOrder = attr.light_priority_order || attr.included_lights || [];
    const lightMeta = attr.light_metadata || {};
    const shedSet = new Set(attr.shed_light_entities || []);

    const rowsHtml = priorityOrder
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
        const isShed = shedSet.has(eid);
        const isLast = idx === priorityOrder.length - 1 && priorityOrder.length > 1;
        const roleTag =
          idx === 0
            ? "Turns Off 1st past 1%"
            : isLast
            ? "🌙 Kept On Last at 1% (Nightlight)"
            : `#${idx + 1} to Turn Off`;

        return `
          <div class="light-item ${isLast ? "is-last" : ""}">
            <div style="display:flex;align-items:center;gap:12px;min-width:0;">
              <div class="light-rank">#${idx + 1}</div>
              <div style="min-width:0;">
                <div style="font-weight:700;font-size:0.9rem;display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
                  <span>${escapeHtml(meta.friendly_name || eid)}</span>
                  <span style="font-size:0.72rem;padding:2px 8px;border-radius:999px;background:rgba(148,163,184,0.14);color:var(--secondary-text-color);">
                    ${escapeHtml(meta.category_label || "Fixture")}
                  </span>
                  <span style="font-size:0.75rem;color:${isShed ? "#fbbf24" : isOn ? "#34d399" : "#94a3b8"};">
                    • ${isShed ? "Shed (Off)" : brVal}
                  </span>
                </div>
                <div style="font-size:0.76rem;color:var(--secondary-text-color);margin-top:2px;">
                  <b>${roleTag}</b> • <code>${escapeHtml(eid)}</code>
                </div>
              </div>
            </div>
            <div style="display:flex;align-items:center;gap:6px;flex-shrink:0;">
              <button class="arrow-btn btn-move-light-up" data-idx="${idx}" ${
                idx === 0 ? "disabled" : ""
              } title="Move Up (Turn off earlier)">⬆️</button>
              <button class="arrow-btn btn-move-light-down" data-idx="${idx}" ${
                idx === priorityOrder.length - 1 ? "disabled" : ""
              } title="Move Down (Keep on longer)">⬇️</button>
            </div>
          </div>
        `;
      })
      .join("");

    return `
      <div class="surface-card">
        <div class="card-title-row">
          <span class="card-title">💡 Progressive Light Shedding Order</span>
          ${
            attr.light_order_locked
              ? `<button class="btn" id="btn-reset-light-order">🔓 Reset to Auto Order</button>`
              : `<span style="font-size:0.78rem;color:var(--secondary-text-color);">🤖 Auto-Ordered by Fixture Type</span>`
          }
        </div>
        <div class="card-subtitle">
          When you press <b>Dimmer (-)</b> and lights are already at <b>1%</b>, Perceptive Lighting turns off <b>one fixture at a time</b> from #1 downward instead of turning the whole room off. Move your brightest ceiling/switch lights to the top (#1) and your softest lamp or LED strip to the bottom.
        </div>

        <div>
          ${rowsHtml || "<div>No lights found in this room.</div>"}
        </div>
      </div>
    `;
  }

  _renderSensorsAndEntitiesSubTab(attr) {
    const sProfiles = attr.sensor_profiles || {};
    const sDynWeights = attr.sensor_dynamic_weights || {};
    const sBreakdown = attr.sensor_breakdown || {};

    const sensorCardsHtml = Object.entries(sProfiles)
      .map(([seid, prof]) => {
        const dynW = Math.round(
          (sDynWeights[seid] ?? prof.base_trust_weight ?? 1.0) * 100
        );
        const curLx = sBreakdown[seid] ?? "--";
        return `
          <div class="field-box">
            <div class="field-label">
              <span>${escapeHtml(seid)}</span>
              <span style="color:#60a5fa;">${curLx} lx</span>
            </div>
            <div class="field-hint">
              📍 ${escapeHtml(prof.ai_location_guess || "Auto")} • Trust Weight: <b>${dynW}%</b> (${prof.samples_analyzed || 0} samples)
            </div>
          </div>
        `;
      })
      .join("");

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

    const entityTogglesHtml = allFlat
      .map((eid) => {
        const isAutoHelper = autoExcludedSet.has(eid);
        const isIncluded = !excludedSet.has(eid) && !isAutoHelper;
        const st = this._hass.states[eid];
        const curVal = st
          ? `${st.state} ${st.attributes.unit_of_measurement || ""}`.trim()
          : "N/A";
        return `
          <label class="rule-item" style="cursor:${isAutoHelper ? "default" : "pointer"};margin-bottom:6px;padding:10px 12px;">
            <div>
              <div style="font-size:0.84rem;font-weight:600;">${escapeHtml(eid)}</div>
              <div style="font-size:0.75rem;color:var(--secondary-text-color);">
                State: <b>${escapeHtml(curVal)}</b>
                ${isAutoHelper ? " • <span style='color:#fbbf24;'>Auto-excluded duplicate helper</span>" : ""}
              </div>
            </div>
            <input type="checkbox" class="chk-toggle-entity" data-eid="${escapeHtml(eid)}" ${
              isIncluded ? "checked" : ""
            } ${isAutoHelper ? "disabled" : ""} />
          </label>
        `;
      })
      .join("");

    return `
      <div class="surface-card">
        <div class="card-title-row">
          <span class="card-title">🔬 Illuminance Sensor Diagnostics</span>
        </div>
        <div class="card-subtitle">
          Live readings and learned placement weights for each illuminance sensor in this room.
        </div>
        <div class="form-grid">
          ${sensorCardsHtml || "<div>No lux sensors profiled.</div>"}
        </div>
      </div>

      <div class="surface-card">
        <div class="card-title-row">
          <span class="card-title">📦 Included Room Entities</span>
        </div>
        <div class="card-subtitle">
          Toggle any entity on or off to include or exclude it from Perceptive Lighting in this room. Changes save automatically.
        </div>
        <div>
          ${entityTogglesHtml}
        </div>
      </div>
    `;
  }

  _renderAiAndTuningSubTab(attr) {
    return `
      <div class="surface-card">
        <div class="card-title-row">
          <span class="card-title">🧠 AI Calibration & Room Reset</span>
          <div style="display:flex;gap:8px;flex-wrap:wrap;">
            <button class="btn primary" id="btn-run-ai">🧠 Optimize with AI Now</button>
            <button class="btn danger" id="btn-reset-this-room">🧹 Reset This Room</button>
          </div>
        </div>
        <div class="card-subtitle">
          Recorded Feedback Events: <b>${attr.observation_count || 0}</b> •
          ${escapeHtml(attr.ai_insight || "Press Dimmer (-) or Brighter (+) whenever you want to teach the room your preferred brightness.")}
        </div>
      </div>

      <div class="surface-card">
        <div class="card-title-row">
          <span class="card-title">⚙️ Controller Timing & Mode</span>
          <button class="btn primary" id="btn-save-timing">💾 Save Timing</button>
        </div>
        <div class="form-grid" style="margin-top:10px;">
          <div class="field-box">
            <div class="field-label"><span>Operating Mode</span></div>
            <select id="tune-op-mode">
              <option value="full_adaptive" ${attr.operating_mode === "full_adaptive" ? "selected" : ""}>Full Adaptive Controller</option>
              <option value="simple_transit" ${attr.operating_mode === "simple_transit" ? "selected" : ""}>Simple Transit</option>
              <option value="sensor_only" ${attr.operating_mode === "sensor_only" ? "selected" : ""}>Sensor-Only (No Control)</option>
            </select>
          </div>
          <div class="field-box">
            <div class="field-label"><span>Color Temperature (CCT)</span></div>
            <select id="tune-cct-mode">
              <option value="external" ${attr.cct_mode === "external" ? "selected" : ""}>Brightness Only (External CCT)</option>
              <option value="perceptive_circadian" ${attr.cct_mode === "perceptive_circadian" ? "selected" : ""}>Brightness + Circadian CCT</option>
            </select>
          </div>
          <div class="field-box">
            <div class="field-label"><span>Occupied On/Off Cooldown (min)</span></div>
            <input type="number" id="tune-cooldown" min="0.5" max="60" step="0.5" value="${attr.on_off_cooldown_minutes ?? 5}" />
          </div>
          <div class="field-box">
            <div class="field-label"><span>Smooth Adjust Interval (min)</span></div>
            <input type="number" id="tune-adjust" min="0.5" max="30" step="0.5" value="${attr.adjust_interval_minutes ?? 3}" />
          </div>
        </div>
      </div>
    `;
  }

  _renderHubView(outAttr, rooms) {
    const sched = outAttr.regime_schedule || {};
    const mode = sched.mode || "hybrid";
    const transMin = sched.transition_minutes ?? 45;
    const morningHr = sched.morning_start_hour ?? 6.0;
    const morningEl = sched.morning_solar_elev ?? sched.morning_solar_elevation ?? -4.0;
    const dayHr = sched.day_start_hour ?? 10.0;
    const dayEl = sched.day_solar_elev ?? sched.day_solar_elevation ?? 14.0;
    const eveHr = sched.evening_start_hour ?? 17.5;
    const eveEl = sched.evening_solar_elev ?? sched.evening_solar_elevation ?? 9.0;
    const ovnStartHr = sched.overnight_start_hour ?? 23.0;
    const ovnEndHr = sched.overnight_end_hour ?? 6.0;
    const gloomShift = sched.gloom_early_evening ?? sched.weather_gloom_shift_enabled ?? true;
    const gloomLux = sched.gloom_sky_lux_threshold ?? sched.weather_gloom_sky_lux_threshold ?? 1500.0;

    const configuredIds = new Set(rooms.map((r) => r.attributes.room_id));
    const allAreas = outAttr.all_ha_areas || [];
    const unconfiguredAreas = allAreas.filter((a) => !configuredIds.has(a.area_id));

    return `
      <div class="surface-card">
        <div class="card-title-row">
          <span class="card-title">🌍 Time-of-Day Schedule & Smooth Cross-Fading</span>
          <button class="btn primary" id="btn-save-hub">💾 Save Global Schedule</button>
        </div>
        <div class="card-subtitle">
          Decide how <b>Morning</b>, <b>Day</b>, <b>Evening</b>, and <b>Overnight</b> are determined across your home. Adjacent regimes smoothly blend over the cross-fade window so there are never sudden brightness drops before sunset.
        </div>

        <div class="form-grid" style="margin-bottom:16px;">
          <div class="field-box">
            <div class="field-label"><span>Detection Mode</span></div>
            <select id="hub-mode">
              <option value="hybrid" ${mode === "hybrid" ? "selected" : ""}>Hybrid (Clock + Sun Elevation + Weather Gloom)</option>
              <option value="clock_only" ${mode === "clock_only" ? "selected" : ""}>Clock Schedule Only</option>
              <option value="solar_only" ${mode === "solar_only" ? "selected" : ""}>Solar Elevation + Overnight Clock</option>
            </select>
          </div>
          <div class="field-box">
            <div class="field-label"><span>Smooth Cross-Fade Window (Minutes)</span></div>
            <input type="number" id="hub-trans-min" min="5" max="120" step="5" value="${transMin}" />
          </div>
          <div class="field-box">
            <div class="field-label"><span>Overcast Early-Evening Shift</span></div>
            <input type="number" id="hub-gloom-lux" min="100" max="10000" step="100" value="${gloomLux}" />
            <label style="display:flex;align-items:center;gap:6px;font-size:0.78rem;margin-top:4px;cursor:pointer;">
              <input type="checkbox" id="hub-gloom-chk" ${gloomShift ? "checked" : ""} />
              <span>Shift toward Evening earlier on dark rainy/overcast afternoons</span>
            </label>
          </div>
        </div>

        <div class="form-grid">
          <div class="field-box">
            <div class="field-label"><span>🌅 Morning (${formatHourDecimal(morningHr)})</span></div>
            <div class="field-hint">Start Hour (0–24) & Dawn Sun Elevation (°)</div>
            <input type="number" id="hub-morn-hr" step="0.25" min="3" max="11" value="${morningHr}" />
            <input type="number" id="hub-morn-el" step="0.5" min="-12" max="10" value="${morningEl}" />
          </div>
          <div class="field-box">
            <div class="field-label"><span>☀️ Day (${formatHourDecimal(dayHr)})</span></div>
            <div class="field-hint">Start Hour (0–24) & Daylight Sun Elevation (°)</div>
            <input type="number" id="hub-day-hr" step="0.25" min="6" max="14" value="${dayHr}" />
            <input type="number" id="hub-day-el" step="0.5" min="0" max="30" value="${dayEl}" />
          </div>
          <div class="field-box">
            <div class="field-label"><span>🌆 Evening (${formatHourDecimal(eveHr)})</span></div>
            <div class="field-hint">Clock Start Hour & Pre-Sunset Sun Elevation (°)</div>
            <input type="number" id="hub-eve-hr" step="0.25" min="14" max="22" value="${eveHr}" />
            <input type="number" id="hub-eve-el" step="0.5" min="-6" max="20" value="${eveEl}" />
          </div>
          <div class="field-box">
            <div class="field-label"><span>🌙 Overnight (${formatHourDecimal(ovnStartHr)} – ${formatHourDecimal(ovnEndHr)})</span></div>
            <div class="field-hint">Overnight Start Hour & Morning End Hour</div>
            <input type="number" id="hub-ovn-start" step="0.25" min="20" max="24" value="${ovnStartHr}" />
            <input type="number" id="hub-ovn-end" step="0.25" min="3" max="10" value="${ovnEndHr}" />
          </div>
        </div>
      </div>

      <div class="surface-card">
        <div class="card-title-row">
          <span class="card-title">🏠 Manage Rooms & Global Reset</span>
          <button class="btn danger" id="btn-reset-all">🧹 Reset All Rooms</button>
        </div>
        <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:12px 0;">
          <select id="hub-add-area" style="max-width:280px;">
            <option value="">-- Add a Home Assistant Area --</option>
            ${unconfiguredAreas
              .map(
                (a) =>
                  `<option value="${escapeHtml(a.area_id)}">${escapeHtml(a.name)}</option>`
              )
              .join("")}
          </select>
          <button class="btn primary" id="btn-add-area">➕ Add Room</button>
        </div>
        <div style="display:flex;gap:8px;flex-wrap:wrap;">
          ${rooms
            .map((r) => {
              const rid = r.attributes.room_id;
              const rname = r.attributes.room_name || rid;
              return `
                <div class="chip-btn" style="cursor:default;">
                  <span>${escapeHtml(rname)}</span>
                  <button class="btn danger btn-del-room" data-room="${escapeHtml(rid)}" style="padding:2px 7px;font-size:0.72rem;">✕</button>
                </div>
              `;
            })
            .join("")}
        </div>
      </div>
    `;
  }

  _bindShellEvents() {
    this.shadowRoot
      .querySelector("#btn-ha-menu")
      ?.addEventListener("click", () => {
        this.dispatchEvent(
          new Event("hass-toggle-menu", { bubbles: true, composed: true })
        );
      });

    this.shadowRoot.querySelectorAll(".room-pill").forEach((btn) => {
      btn.addEventListener("click", () => {
        this._activeTab = btn.getAttribute("data-tab");
        this._render();
      });
    });

    this.shadowRoot.querySelectorAll(".subnav-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        this._roomSubTab = btn.getAttribute("data-subtab");
        this._render();
      });
    });
  }

  _bindRoomEvents(sensor) {
    const attr = this._getEffectiveRoomAttr(sensor);
    const roomId = attr.room_id;
    const roomName = attr.room_name || roomId;
    const isLocked = Boolean(attr.automation_locked);

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
      .querySelector("#btn-chip-dark")
      ?.addEventListener("click", () => {
        this._callService(
          "calibrate_feels_dark_now",
          { room_id: roomId },
          "Turned lights ON & raised Turn-On threshold"
        );
      });

    this.shadowRoot
      .querySelector("#btn-chip-bright")
      ?.addEventListener("click", () => {
        this._callService(
          "calibrate_feels_bright_now",
          { room_id: roomId },
          "Turned lights OFF & lowered Turn-On threshold"
        );
      });

    this.shadowRoot
      .querySelector("#btn-chip-capture")
      ?.addEventListener("click", () => {
        const sname = prompt(
          `Save current ${roomName} lights as a Scene. Enter a scene name:`
        );
        if (sname && sname.trim()) {
          this._callService(
            "capture_adaptive_scene",
            { room_id: roomId, name: sname.trim() },
            `Saved Scene "${sname.trim()}"`
          );
        }
      });

    this.shadowRoot
      .querySelector("#btn-chip-lock")
      ?.addEventListener("click", () => {
        this._applyOptimisticPatch(roomId, { automation_locked: !isLocked });
        this._callService(
          isLocked ? "unlock_room" : "lock_room",
          { room_id: roomId },
          isLocked ? `${roomName} unlocked` : `${roomName} locked`
        );
      });

    // Sub-tab 1: Scenes & Time of Day
    this.shadowRoot.querySelectorAll("[data-select-regime]").forEach((btn) => {
      btn.addEventListener("click", () => {
        this._selectedRegime = btn.getAttribute("data-select-regime");
        this._render();
      });
    });

    const brRange = this.shadowRoot.querySelector("#reg-target-br");
    if (brRange) {
      brRange.addEventListener("input", (ev) => {
        const lbl = this.shadowRoot.querySelector("#lbl-reg-br");
        if (lbl) lbl.textContent = `${ev.target.value}%`;
      });
    }

    this.shadowRoot
      .querySelector("#btn-save-active-regime")
      ?.addEventListener("click", () => {
        const rkey = this._selectedRegime || attr.time_regime || "evening";
        const nextRegimeConfigs = JSON.parse(
          JSON.stringify(attr.regime_configs || {})
        );
        nextRegimeConfigs[rkey] = {
          ...(nextRegimeConfigs[rkey] || {}),
          occupied_scene:
            this.shadowRoot.querySelector("#reg-occ-scene")?.value || "all_lights",
          off_scene:
            this.shadowRoot.querySelector("#reg-off-scene")?.value || "off",
          off_scene_when_unoccupied: Boolean(
            this.shadowRoot.querySelector("#reg-off-unocc")?.checked
          ),
          off_scene_when_bright: Boolean(
            this.shadowRoot.querySelector("#reg-off-bright")?.checked
          ),
          target_brightness_pct: parseInt(
            this.shadowRoot.querySelector("#reg-target-br")?.value || "60",
            10
          ),
          turn_on_lux: parseFloat(
            this.shadowRoot.querySelector("#reg-turn-on-lux")?.value || "10"
          ),
          target_total_lux: parseFloat(
            this.shadowRoot.querySelector("#reg-target-tot-lux")?.value || "12"
          ),
          shed_count: parseInt(
            this.shadowRoot.querySelector("#reg-shed-count")?.value || "0",
            10
          ),
          illum_model:
            this.shadowRoot.querySelector("#reg-illum-model")?.value ||
            "weber_fechner",
          weather_model:
            this.shadowRoot.querySelector("#reg-weather-model")?.value ||
            "cave_contrast",
          season_model:
            this.shadowRoot.querySelector("#reg-season-model")?.value ||
            "season_neutral",
        };

        this._applyOptimisticPatch(roomId, { regime_configs: nextRegimeConfigs });
        this._callService(
          "update_room_config",
          { room_id: roomId, regime_configs: nextRegimeConfigs },
          `Saved ${REGIME_META[rkey]?.label || rkey} settings`
        );
      });

    this.shadowRoot
      .querySelector("#btn-toggle-add-rule")
      ?.addEventListener("click", () => {
        this._showAddRuleForm = !this._showAddRuleForm;
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
          this.shadowRoot.querySelector("#rule-trigger-type")?.value ||
          "presence_only";
        const entityId =
          trigType === "presence_only"
            ? "presence_only"
            : this.shadowRoot.querySelector("#rule-entity")?.value || "presence_only";
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

        this._showAddRuleForm = false;
        const nextRegimeConfigs = JSON.parse(
          JSON.stringify(attr.regime_configs || {})
        );
        if (entityId === "presence_only") {
          const targetRegimes =
            regime === "any"
              ? ["morning", "day", "evening", "overnight"]
              : [regime];
          targetRegimes.forEach((rk) => {
            nextRegimeConfigs[rk] = {
              ...(nextRegimeConfigs[rk] || {}),
              occupied_scene: sceneId,
            };
          });
        }
        this._applyOptimisticPatch(roomId, {
          scene_triggers: nextTriggers,
          regime_configs: nextRegimeConfigs,
        });
        this._callService(
          "update_room_config",
          {
            room_id: roomId,
            scene_triggers: nextTriggers,
            regime_configs: nextRegimeConfigs,
          },
          "Added scene rule"
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
            "Removed scene rule"
          );
        }
      });
    });

    // Sub-tab 2: Instant 0ms Optimistic Light Reordering
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
            `Moved ${ (attr.light_metadata?.[nextOrder[idx - 1]]?.friendly_name) || nextOrder[idx - 1] } up to #${idx}`
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
            `Moved ${ (attr.light_metadata?.[nextOrder[idx + 1]]?.friendly_name) || nextOrder[idx + 1] } down to #${idx + 2}`
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
          { room_id: roomId, light_order_locked: false, light_priority_order: [] },
          "Reset to automatic fixture order"
        );
      });

    // Sub-tab 3: Auto-saving Entity Inclusion Checkboxes
    this.shadowRoot.querySelectorAll(".chk-toggle-entity").forEach((chk) => {
      chk.addEventListener("change", () => {
        const excluded = [];
        this.shadowRoot.querySelectorAll(".chk-toggle-entity").forEach((c) => {
          if (!c.disabled && !c.checked) {
            excluded.push(c.getAttribute("data-eid"));
          }
        });
        this._applyOptimisticPatch(roomId, { excluded_entities: excluded });
        this._callService(
          "update_room_config",
          { room_id: roomId, excluded_entities: excluded },
          "Updated included room entities"
        );
      });
    });

    // Sub-tab 4: AI & Timing
    this.shadowRoot
      .querySelector("#btn-run-ai")
      ?.addEventListener("click", () => {
        this._callService(
          "optimize_with_ai",
          { room_id: roomId },
          `Optimizing ${roomName} with AI...`
        );
      });

    this.shadowRoot
      .querySelector("#btn-reset-this-room")
      ?.addEventListener("click", () => {
        if (confirm(`Reset learned thresholds and nudges for ${roomName}?`)) {
          delete this._optimisticPatches[roomId];
          this._callService(
            "reset_room",
            { room_id: roomId },
            `${roomName} reset to defaults`
          );
        }
      });

    this.shadowRoot
      .querySelector("#btn-save-timing")
      ?.addEventListener("click", () => {
        const opMode = this.shadowRoot.querySelector("#tune-op-mode")?.value;
        const cctMode = this.shadowRoot.querySelector("#tune-cct-mode")?.value;
        const cooldown = parseFloat(
          this.shadowRoot.querySelector("#tune-cooldown")?.value || "5"
        );
        const adjust = parseFloat(
          this.shadowRoot.querySelector("#tune-adjust")?.value || "3"
        );
        this._callService(
          "update_room_config",
          {
            room_id: roomId,
            operating_mode: opMode,
            cct_mode: cctMode,
            on_off_cooldown_minutes: cooldown,
            adjust_interval_minutes: adjust,
          },
          "Saved controller timing"
        );
      });
  }

  _bindHubEvents() {
    this.shadowRoot
      .querySelector("#btn-save-hub")
      ?.addEventListener("click", () => {
        const regimeSchedule = {
          mode: this.shadowRoot.querySelector("#hub-mode")?.value || "hybrid",
          transition_minutes: parseFloat(
            this.shadowRoot.querySelector("#hub-trans-min")?.value || "45"
          ),
          gloom_sky_lux_threshold: parseFloat(
            this.shadowRoot.querySelector("#hub-gloom-lux")?.value || "1500"
          ),
          gloom_early_evening: Boolean(
            this.shadowRoot.querySelector("#hub-gloom-chk")?.checked
          ),
          morning_start_hour: parseFloat(
            this.shadowRoot.querySelector("#hub-morn-hr")?.value || "6"
          ),
          morning_solar_elev: parseFloat(
            this.shadowRoot.querySelector("#hub-morn-el")?.value || "-4"
          ),
          day_start_hour: parseFloat(
            this.shadowRoot.querySelector("#hub-day-hr")?.value || "10"
          ),
          day_solar_elev: parseFloat(
            this.shadowRoot.querySelector("#hub-day-el")?.value || "14"
          ),
          evening_start_hour: parseFloat(
            this.shadowRoot.querySelector("#hub-eve-hr")?.value || "17.5"
          ),
          evening_solar_elev: parseFloat(
            this.shadowRoot.querySelector("#hub-eve-el")?.value || "9"
          ),
          overnight_start_hour: parseFloat(
            this.shadowRoot.querySelector("#hub-ovn-start")?.value || "23"
          ),
          overnight_end_hour: parseFloat(
            this.shadowRoot.querySelector("#hub-ovn-end")?.value || "6"
          ),
        };
        this._callService(
          "update_hub_config",
          { regime_schedule: regimeSchedule },
          "Saved global Time-of-Day schedule"
        );
      });

    this.shadowRoot
      .querySelector("#btn-reset-all")
      ?.addEventListener("click", () => {
        if (confirm("Reset learned thresholds and nudges for ALL rooms?")) {
          this._optimisticPatches = {};
          this._callService(
            "reset_room",
            { room_id: "all" },
            "All rooms reset to clean defaults"
          );
        }
      });

    this.shadowRoot
      .querySelector("#btn-add-area")
      ?.addEventListener("click", () => {
        const areaId = this.shadowRoot.querySelector("#hub-add-area")?.value;
        if (areaId) {
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
    background: var(--ha-card-background, var(--card-background-color, #1e293b));
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
              ${escapeHtml((attr.time_regime || "day").toUpperCase())} • <b>${attr.raw_lux} lx</b> (Turn-On ≤ ${attr.turn_on_lux_threshold} lx) • Target: <b>${attr.recommended_brightness_pct}%</b>
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
          <button class="btn-action" id="btn-feels-dark-now">🌑 Too Dark</button>
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
                  (${isOcc ? "Occupied" : "Vacant"} • ${a.raw_lux} lx)
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
