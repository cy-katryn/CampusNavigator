/** Text to speech: one utterance at a time, cancelled before the next one starts. */
export function pickVoice(lang: string): SpeechSynthesisVoice | null {
  const voices = window.speechSynthesis ? window.speechSynthesis.getVoices() : [];
  const wanted = lang || 'en-US';
  return (
    voices.find((v) => v.lang === wanted) ||
    voices.find((v) => v.lang && v.lang.slice(0, 2) === wanted.slice(0, 2)) ||
    voices.find((v) => /^en/i.test(v.lang)) ||
    null
  );
}

export function cancelSpeech(): void {
  if (window.speechSynthesis) window.speechSynthesis.cancel();
}

export interface SpeakHandlers {
  onStart?(): void;
  onEnd?(): void;
}

/** Returns false when nothing will be said, so the caller can settle the face. */
export function speak(text: string, lang: string, handlers: SpeakHandlers = {}): boolean {
  if (!text || !window.speechSynthesis) return false;
  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(text);
  const voice = pickVoice(lang);
  if (voice) utterance.voice = voice;
  utterance.rate = 1.0;
  utterance.pitch = 1.0;
  utterance.onstart = () => handlers.onStart?.();
  utterance.onend = () => handlers.onEnd?.();
  utterance.onerror = () => handlers.onEnd?.();
  window.speechSynthesis.speak(utterance);
  return true;
}
