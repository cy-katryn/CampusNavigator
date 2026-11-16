import { Mic, Volume2, VolumeX } from 'lucide-react';

export type AvatarState = 'idle' | 'listening' | 'thinking' | 'speaking' | 'error';

export const AVATAR_LABELS: Record<AvatarState, string> = {
  idle: 'idle',
  listening: 'listening…',
  thinking: 'thinking…',
  speaking: 'speaking…',
  error: 'no connection',
};

export interface AvatarDockProps {
  state: AvatarState;
  voiceOn: boolean;
  lang: string;
  micAvailable: boolean;
  micRecording: boolean;
  hint: string;
  onMic(): void;
  onToggleVoice(): void;
  onLangChange(lang: string): void;
}

/* A robot bust, drawn once; the five state classes in styles.css are the only thing
   JavaScript ever changes on it. */
function Robot({ state }: { state: AvatarState }) {
  return (
    <svg
      viewBox="0 0 200 200"
      className={'avatar h-[88px] w-[88px] flex-none ' + state}
      role="img"
      aria-label="Assistant robot face"
    >
      <defs>
        <radialGradient id="avatar-halo" cx="46%" cy="38%" r="62%">
          <stop offset="0%" className="halo-in" />
          <stop offset="100%" className="halo-out" />
        </radialGradient>
        <linearGradient id="avatar-shell" x1="22%" y1="0%" x2="78%" y2="100%">
          <stop offset="0%" className="shell-hi" />
          <stop offset="100%" className="shell-lo" />
        </linearGradient>
        <linearGradient id="avatar-torso" x1="24%" y1="0%" x2="76%" y2="100%">
          <stop offset="0%" className="torso-hi" />
          <stop offset="100%" className="torso-lo" />
        </linearGradient>
        <filter id="avatar-eyeglow" x="-80%" y="-80%" width="260%" height="260%">
          <feDropShadow className="eglow" dx="0" dy="0" stdDeviation="3.2" />
        </filter>
      </defs>

      <circle cx="100" cy="92" r="86" fill="url(#avatar-halo)" />

      {/* sweater: a cropped bust, raglan seams running from the neckline to the joints */}
      <path className="torso" d="M34 180 C34 124 50 137 72 143 L128 143 C150 137 166 124 166 180 Z" />
      <path className="collar" d="M78 146 Q66 155 54 169.64" />
      <path className="collar" d="M122 146 Q134 155 146 169.64" />
      <circle className="joint" cx="40" cy="162.61" r="13" />
      <circle className="joint" cx="160" cy="162.61" r="13" />
      <circle className="jointin" cx="40" cy="162.61" r="4.55" />
      <circle className="jointin" cx="160" cy="162.61" r="4.55" />
      <rect className="badge" x="84" y="155.61" width="32" height="14" rx="4" />
      <rect className="badge-l" x="90" y="159.61" width="20" height="2.4" rx="1.2" />
      <rect className="badge-l" x="90" y="164.61" width="13" height="2.4" rx="1.2" />

      {/* neck: red collar flange, dark column, one rib */}
      <rect className="flange" x="72" y="130" width="56" height="15" rx="7" />
      <rect className="neck" x="86" y="112" width="28" height="24" rx="6" />
      <rect className="rib" x="90" y="124.6" width="20" height="3" rx="1.5" />

      {/* antenna: slow pulse is the idle heartbeat */}
      <rect className="stem" x="98.5" y="12" width="3" height="16" rx="1.5" />
      <circle className="bulb" cx="100" cy="9" r="6.5" />
      <circle className="bulb-hl" cx="98" cy="6.5" r="2" />

      {/* side pods */}
      <circle className="pod" cx="31" cy="80" r="10.5" />
      <circle className="pod" cx="169" cy="80" r="10.5" />
      <circle className="pod-in" cx="31" cy="80" r="3.6" />
      <circle className="pod-in" cx="169" cy="80" r="3.6" />

      {/* head */}
      <rect className="skull" x="40" y="26" width="120" height="92" rx="26" />
      <rect className="visor" x="49" y="38" width="102" height="68" rx="16" />

      <g className="eyes">
        <circle className="eye" cx="80" cy="63" r="7" />
        <circle className="eye" cx="120" cy="63" r="7" />
      </g>

      <path className="smile" d="M85 81 Q100 96 115 81" />
      <path className="frown" d="M85 89 Q100 74 115 89" />
      <path
        className="xarms"
        d="M71.5 54.5 L88.5 71.5 M88.5 54.5 L71.5 71.5 M111.5 54.5 L128.5 71.5 M128.5 54.5 L111.5 71.5"
      />

      <g className="earicon" transform="translate(61.6 53.6) scale(1.7)">
        <path d="M6 8.5a6.5 6.5 0 1 1 13 0c0 6-6 6-6 10a3.5 3.5 0 1 1-7 0" />
        <path d="M15 8.5a2.5 2.5 0 0 0-5 0v1a2 2 0 1 1 0 4" />
      </g>
      <path className="waves2 w1" d="M106 63 Q117 74 106 85" />
      <path className="waves2 w2" d="M114 56 Q130 74 114 92" />

      <g className="dots">
        <circle cx="84" cy="69" r="4.5" />
        <circle cx="100" cy="69" r="4.5" />
        <circle cx="116" cy="69" r="4.5" />
      </g>

      <g className="tdots">
        <circle cx="84" cy="85" r="6.5" />
        <circle cx="100" cy="85" r="6.5" />
        <circle cx="116" cy="85" r="6.5" />
      </g>
    </svg>
  );
}

const VOICE_BTN =
  'inline-flex cursor-pointer items-center gap-[7px] rounded-[11px] border border-line bg-panel-soft ' +
  'px-[13px] py-2 text-[13px] text-ink transition hover:border-line-strong ' +
  'disabled:cursor-not-allowed disabled:opacity-45';

export function AvatarDock(props: AvatarDockProps) {
  const { state, voiceOn, lang, micAvailable, micRecording, hint } = props;
  return (
    <div className="relative z-[2] mx-4 my-2 flex items-center gap-3 rounded-[14px] border border-line bg-panel px-3.5 py-2.5 shadow-[0_8px_20px_var(--accent-ring)]">
      <Robot state={state} />
      <div className="flex min-w-0 flex-1 flex-col gap-[7px]">
        <div className="self-start rounded-full border border-line bg-panel-soft px-[9px] py-0.5 text-[10px] uppercase tracking-[0.14em] text-muted">
          {AVATAR_LABELS[state]}
        </div>

        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            title="Speak to the assistant (speech to text)"
            disabled={!micAvailable}
            onClick={props.onMic}
            className={
              VOICE_BTN +
              (micRecording
                ? ' animate-[pulseRing_1.1s_infinite] border-danger bg-danger-soft text-danger'
                : '')
            }
          >
            <Mic size={16} strokeWidth={1.9} aria-hidden="true" />
            <span>Speak</span>
          </button>

          <button
            type="button"
            title="Read replies aloud (text to speech)"
            onClick={props.onToggleVoice}
            className={VOICE_BTN + (voiceOn ? ' border-brand bg-accent-soft text-brand' : '')}
          >
            {voiceOn ? (
              <Volume2 size={16} strokeWidth={1.9} aria-hidden="true" />
            ) : (
              <VolumeX size={16} strokeWidth={1.9} aria-hidden="true" />
            )}
            <span>Voice</span>
          </button>

          <label title="Recognition language">
            <select
              value={lang}
              onChange={(event) => props.onLangChange(event.target.value)}
              className="cursor-pointer rounded-[11px] border border-line bg-panel-soft px-2.5 py-2 font-sans text-[13px] text-ink"
            >
              <option value="en-US">English</option>
              <option value="zh-HK">廣東話</option>
              <option value="zh-CN">普通话</option>
            </select>
          </label>
        </div>

        <p className="m-0 text-[11px] leading-snug text-muted">{hint}</p>
      </div>
    </div>
  );
}
