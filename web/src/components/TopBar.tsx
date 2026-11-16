import type { Status } from '../types';

export interface TopBarProps {
  status: Status | null;
  engineOffline: boolean;
  onOpenSettings(): void;
}

function pillText(status: Status | null, engineOffline: boolean): { text: string; ok: boolean; title: string } {
  if (engineOffline) return { text: 'engine offline', ok: false, title: 'the engine is not answering' };
  const connected = Boolean(status && status.model);
  if (connected && status) {
    return {
      text: 'model: ' + status.model.split('/').pop()!.replace(':free', ''),
      ok: true,
      title: status.mode + '\nkey: present',
    };
  }
  return {
    text: 'no model connected',
    ok: false,
    title:
      (status ? status.mode : 'connecting…') +
      '\nUse the gear to add an OpenRouter key; every feature still works without one',
  };
}

export function TopBar({ status, engineOffline, onOpenSettings }: TopBarProps) {
  const pill = pillText(status, engineOffline);
  const counts = status && status.knowledge
    ? 'Loaded: ' +
      status.nodes +
      ' places, ' +
      status.edges +
      ' links, ' +
      status.knowledge.documents +
      ' knowledge records (' +
      status.knowledge.with_opening_hours +
      ' with published hours).'
    : '';

  return (
    <header className="topbar">
      <div className="brand">
        <span className="logo" aria-hidden="true">
          ◈
        </span>
        <div>
          <h1>PolyU Campus Navigator</h1>
          <p className="sub">COMP4431 Artificial Intelligence · Group 21</p>
        </div>
      </div>

      <div className="status">
        <span className={'pill ' + (pill.ok ? 'ok' : 'warn')} title={pill.title}>
          {pill.text}
        </span>

        {/* (i) hover/focus card: where the data comes from. */}
        <div className="tip-wrap">
          <button
            className="round-btn"
            type="button"
            aria-label="Where does this data come from?"
            aria-describedby="info-pop"
          >
            i
          </button>
          <div className="tip-card" id="info-pop" role="tooltip">
            <h3>Where this data comes from</h3>
            <p>Scraped once from two official PolyU websites. </p>
            <ul>
              <li>
                <b>Campus Map</b> — 561 POIs with waypoints and trilingual names
                <br />
                <span className="src">polyu.edu.hk/campus-map</span>
              </li>
              <li>
                <b>GreenMap@PolyU</b> — 315 recycling, water and green facilities
                <br />
                <span className="src">polyu.edu.hk/greenmap</span>
              </li>
            </ul>
            <p className="tip-foot">
              Walkway links are <b>hand-authored</b> on top of published coordinates and may contain
              errors. Stairs and lifts are not modelled. Opening hours are quoted only where PolyU
              publishes them.
            </p>
            <p className="tip-foot">{counts}</p>
          </div>
        </div>

        <button
          className="round-btn"
          type="button"
          aria-label="Model settings"
          title="Model settings (API key)"
          onClick={onOpenSettings}
        >
          ⚙
        </button>
      </div>
    </header>
  );
}
