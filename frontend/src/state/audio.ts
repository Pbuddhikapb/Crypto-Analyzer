// Web Audio API synthesizer for institutional audio alerts without external mp3 dependencies

class SoundEngine {
  private ctx: AudioContext | null = null;
  private muted: boolean = false;

  constructor() {
    if (typeof window !== 'undefined') {
      const saved = localStorage.getItem('terminal_sound_muted');
      this.muted = saved === 'true';
    }
  }

  private getContext(): AudioContext | null {
    if (typeof window === 'undefined') return null;
    if (!this.ctx) {
      const AudioCtx = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      if (AudioCtx) {
        this.ctx = new AudioCtx();
      }
    }
    if (this.ctx && this.ctx.state === 'suspended') {
      this.ctx.resume().catch(() => {});
    }
    return this.ctx;
  }

  public isMuted(): boolean {
    return this.muted;
  }

  public toggleMute(): boolean {
    this.muted = !this.muted;
    if (typeof window !== 'undefined') {
      localStorage.setItem('terminal_sound_muted', String(this.muted));
    }
    return this.muted;
  }

  public playWhaleChime(side: 'BUY' | 'SELL'): void {
    if (this.muted) return;
    try {
      const ctx = this.getContext();
      if (!ctx) return;

      const now = ctx.currentTime;
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();

      osc.connect(gain);
      gain.connect(ctx.destination);

      if (side === 'BUY') {
        // High harmonic upward chime (880Hz -> 1320Hz)
        osc.type = 'triangle';
        osc.frequency.setValueAtTime(880, now);
        osc.frequency.exponentialRampToValueAtTime(1320, now + 0.12);

        gain.gain.setValueAtTime(0.001, now);
        gain.gain.linearRampToValueAtTime(0.18, now + 0.03);
        gain.gain.exponentialRampToValueAtTime(0.001, now + 0.35);

        osc.start(now);
        osc.stop(now + 0.36);
      } else {
        // Deep low-frequency bass drop (180Hz -> 85Hz)
        osc.type = 'sine';
        osc.frequency.setValueAtTime(180, now);
        osc.frequency.exponentialRampToValueAtTime(85, now + 0.18);

        gain.gain.setValueAtTime(0.001, now);
        gain.gain.linearRampToValueAtTime(0.22, now + 0.04);
        gain.gain.exponentialRampToValueAtTime(0.001, now + 0.4);

        osc.start(now);
        osc.stop(now + 0.41);
      }
    } catch {
      // Ignore audio synthesis errors on locked autoplay policies
    }
  }

  public playCascadeAlert(): void {
    if (this.muted) return;
    try {
      const ctx = this.getContext();
      if (!ctx) return;

      const now = ctx.currentTime;
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();

      osc.connect(gain);
      gain.connect(ctx.destination);

      osc.type = 'sawtooth';
      osc.frequency.setValueAtTime(520, now);
      osc.frequency.linearRampToValueAtTime(740, now + 0.1);
      osc.frequency.linearRampToValueAtTime(520, now + 0.2);
      osc.frequency.linearRampToValueAtTime(740, now + 0.3);

      gain.gain.setValueAtTime(0.001, now);
      gain.gain.linearRampToValueAtTime(0.2, now + 0.05);
      gain.gain.exponentialRampToValueAtTime(0.001, now + 0.45);

      osc.start(now);
      osc.stop(now + 0.46);
    } catch {
      // Ignore
    }
  }
}

export const soundEngine = new SoundEngine();
