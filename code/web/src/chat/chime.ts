let context: AudioContext | null = null;

// Two short soft notes for live notifications; silently skipped when the
// browser blocks audio (no user gesture yet).
export function chime(): void {
  try {
    const audio = (context ??= new AudioContext());
    if (audio.state === "suspended") void audio.resume();
    const start = audio.currentTime;
    [880, 1320].forEach((frequency, index) => {
      const at = start + index * 0.12;
      const oscillator = audio.createOscillator();
      const gain = audio.createGain();
      oscillator.frequency.value = frequency;
      gain.gain.setValueAtTime(0.0001, at);
      gain.gain.exponentialRampToValueAtTime(0.08, at + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, at + 0.3);
      oscillator.connect(gain).connect(audio.destination);
      oscillator.start(at);
      oscillator.stop(at + 0.3);
    });
  } catch {
    // Audio is a nice-to-have.
  }
}
