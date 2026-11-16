import { useCallback, useEffect, useRef, useState } from 'react';
import { fetchGraph, fetchStatus, resetSession, sendChat } from './api';
import { ChatPanel } from './components/ChatPanel';
import { MapPanel } from './components/MapPanel';
import { SettingsDialog } from './components/SettingsDialog';
import { TopBar } from './components/TopBar';
import type { AvatarState } from './components/AvatarDock';
import type { CampusMap } from './lib/campusMap';
import { cancelSpeech, speak } from './lib/speech';
import { useSpeechRecognition } from './hooks/useSpeechRecognition';
import type { ChatMessage, ChatPayload, FacilityCard, Graph, RoutePayload, Status } from './types';

const GREETING =
  'Hi! I am PolyU Campus Navigator Chatbot. I am here to help you navigate the PolyU. ' +
  'Whether you need walking directions, green facilities, or essential amenities, I have your back! ';

export default function App() {
  const [status, setStatus] = useState<Status | null>(null);
  const [engineOffline, setEngineOffline] = useState(false);
  const [graph, setGraph] = useState<Graph | null>(null);
  const [route, setRoute] = useState<RoutePayload | null>(null);
  const [facilities, setFacilities] = useState<FacilityCard[]>([]);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState('');
  const [focusSignal, setFocusSignal] = useState(0);
  const [avatar, setAvatarState] = useState<AvatarState>('idle');
  const [voiceOn, setVoiceOn] = useState(true);
  const [lang, setLang] = useState('en-US');
  const [settingsOpen, setSettingsOpen] = useState(false);

  const mapRef = useRef<CampusMap | null>(null);
  const nextId = useRef(1);
  const speaking = useRef(false);
  const listening = useRef(false);
  const recover = useRef<number | undefined>(undefined);

  /* The face has five mutually exclusive states, and this is the only way to set
     one. An error is a flash, not a home: it clears itself. */
  const setAvatar = useCallback((mode: AvatarState) => {
    window.clearTimeout(recover.current);
    setAvatarState(mode);
    if (mode === 'error') {
      recover.current = window.setTimeout(
        () => setAvatarState(speaking.current ? 'speaking' : 'idle'),
        2600,
      );
    }
  }, []);

  const push = useCallback((role: ChatMessage['role'], payload: ChatPayload | string) => {
    const id = nextId.current;
    nextId.current += 1;
    setMessages((previous) => [...previous, { id, role, payload }]);
  }, []);

  const say = useCallback(
    (text: string | undefined) => {
      const started = voiceOn && text ? speak(text, lang, {
        onStart: () => {
          speaking.current = true;
          setAvatar('speaking');
        },
        onEnd: () => {
          speaking.current = false;
          setAvatar(listening.current ? 'listening' : 'idle');
        },
      }) : false;
      // nothing is going to be said -- do not leave the face stuck on "thinking"
      if (!started) setAvatar(listening.current ? 'listening' : 'idle');
    },
    [lang, setAvatar, voiceOn],
  );

  const send = useCallback(
    async (text: string) => {
      const trimmed = (text || '').trim();
      if (!trimmed) return;
      push('user', trimmed);
      setDraft('');
      setAvatar('thinking');
      try {
        const payload = await sendChat(trimmed);
        if (payload.error) {
          push('bot', { text: 'Server error: ' + payload.error, route: null, facilities: [] });
          setAvatar('idle');
          return;
        }
        push('bot', payload);
        setRoute(payload.route);
        setFacilities(payload.facilities || []);
        say(payload.speech);
      } catch (error) {
        push('bot', { text: 'Could not reach the engine: ' + String(error), route: null, facilities: [] });
        // the one thing the face knows that the text does not: the engine is gone
        setAvatar('error');
      }
    },
    [push, say, setAvatar],
  );

  const recognition = useSpeechRecognition({
    onTranscript: (text, isFinal) => {
      setDraft(text);
      if (isFinal) void send(text);
    },
    onListeningChange: (isListening) => {
      listening.current = isListening;
      if (isListening) setAvatar('listening');
      else if (!speaking.current) setAvatar('idle');
    },
  });

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const next = await fetchStatus();
        if (!cancelled) setStatus(next);
      } catch {
        if (!cancelled) setEngineOffline(true);
      }
      try {
        const loaded = await fetchGraph();
        if (!cancelled) setGraph(loaded);
      } catch {
        // the map is decoration around a working chat: a failure here is survivable
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    push('bot', { text: GREETING, route: null, facilities: [] });
    // the greeting is the opening turn, not a reaction to state
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const reset = async () => {
    await resetSession();
    setMessages([]);
    setRoute(null);
    setFacilities([]);
    mapRef.current?.clearOverlays();
    cancelSpeech();
    speaking.current = false;
    setAvatar('idle');
    push('bot', { text: 'Conversation cleared. Where would you like to go?', route: null, facilities: [] });
  };

  return (
    <>
      <TopBar status={status} engineOffline={engineOffline} onOpenSettings={() => setSettingsOpen(true)} />

      <SettingsDialog
        open={settingsOpen}
        status={status}
        onClose={() => setSettingsOpen(false)}
        onSaved={setStatus}
      />

      <main className="layout">
        <ChatPanel
          messages={messages}
          draft={draft}
          avatar={avatar}
          voiceOn={voiceOn}
          lang={lang}
          micAvailable={recognition.supported}
          micRecording={recognition.listening}
          hint={recognition.hint}
          focusSignal={focusSignal}
          onDraftChange={setDraft}
          onSend={(text) => void send(text)}
          onReset={() => void reset()}
          onFacilityClick={(name) => void send('how do I get to ' + name + '?')}
          onMic={() => {
            cancelSpeech();
            recognition.start(lang);
          }}
          onToggleVoice={() => {
            setVoiceOn((on) => {
              if (on) cancelSpeech();
              return !on;
            });
          }}
          onLangChange={setLang}
        />

        <MapPanel
          graph={graph}
          route={route}
          facilities={facilities}
          mapRef={mapRef}
          onPrefill={(text) => {
            setDraft(text);
            setFocusSignal((n) => n + 1);
          }}
          onSend={(text) => void send(text)}
        />
      </main>
    </>
  );
}
