import { useCallback, useEffect, useRef, useState } from 'react';

export interface RecognitionHandlers {
  /** Interim text replaces the composer; a final phrase is sent. */
  onTranscript(text: string, isFinal: boolean): void;
  onListeningChange?(listening: boolean): void;
}

export interface Recognition {
  supported: boolean;
  listening: boolean;
  hint: string;
  start(lang: string): void;
}

/**
 * Microphone input through the Web Speech API. Wrapped so the rest of the UI never
 * touches the vendor-prefixed constructor, and so a browser without it gets an
 * explanation instead of a dead button.
 */
export function useSpeechRecognition(handlers: RecognitionHandlers): Recognition {
  const supported =
    typeof window !== 'undefined' &&
    Boolean(window.SpeechRecognition || window.webkitSpeechRecognition);
  const [listening, setListening] = useState(false);
  const [hint, setHint] = useState(
    supported
      ? ''
      : 'Speech input needs a browser with the Web Speech API (Chrome or Edge). ' +
          'Text input and spoken replies still work.',
  );
  const recognition = useRef<SpeechRecognitionLike | null>(null);
  const handlersRef = useRef(handlers);
  handlersRef.current = handlers;

  useEffect(() => {
    const Constructor = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!Constructor) return;
    const rec = new Constructor();
    rec.interimResults = true;
    rec.continuous = false;

    rec.onstart = () => {
      setListening(true);
      handlersRef.current.onListeningChange?.(true);
    };
    rec.onresult = (event) => {
      let interim = '';
      let final = '';
      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        const chunk = event.results[i][0].transcript;
        if (event.results[i].isFinal) final += chunk;
        else interim += chunk;
      }
      handlersRef.current.onTranscript(final || interim, Boolean(final));
      if (final) rec.stop();
    };
    rec.onerror = (event) => {
      setHint(
        'Microphone error: ' +
          event.error +
          (event.error === 'not-allowed' ? ' (allow microphone access in the browser)' : ''),
      );
    };
    rec.onend = () => {
      setListening(false);
      handlersRef.current.onListeningChange?.(false);
    };

    recognition.current = rec;
    return () => {
      recognition.current = null;
    };
  }, []);

  const start = useCallback((lang: string) => {
    const rec = recognition.current;
    if (!rec) return;
    rec.lang = lang;
    try {
      rec.start();
    } catch {
      // already running: starting twice throws, and there is nothing to do about it
    }
  }, []);

  return { supported, listening, hint, start };
}
