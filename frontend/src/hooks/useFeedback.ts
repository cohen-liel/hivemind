import { useCallback } from 'react';

interface FeedbackOptions {
  enabled?: boolean;
}

interface FeedbackActions {
  onTaskComplete: () => void;
  onTaskFailed: () => void;
  onAllComplete: () => void;
  onTaskStarted: () => void;
}

export function useFeedback({ enabled = true }: FeedbackOptions = {}): FeedbackActions {
  const playTone = useCallback((frequency: number, duration: number, volume: number): void => {
    if (!enabled) return;
    try {
      const ctx = new AudioContext();
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.frequency.value = frequency;
      osc.type = 'sine';
      gain.gain.value = volume;
      gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + duration);
      osc.start(ctx.currentTime);
      osc.stop(ctx.currentTime + duration);
    } catch {
      // AudioContext not available — silent fallback
    }
  }, [enabled]);

  const haptic = useCallback((pattern: number | number[]): void => {
    if (!enabled) return;
    try {
      if (navigator.vibrate) {
        navigator.vibrate(pattern);
      }
    } catch {
      // Vibration API not available
    }
  }, [enabled]);

  const onTaskComplete = useCallback((): void => {
    playTone(880, 0.12, 0.08);
    haptic(30);
  }, [playTone, haptic]);

  const onTaskFailed = useCallback((): void => {
    playTone(220, 0.2, 0.06);
    haptic([20, 30, 20]);
  }, [playTone, haptic]);

  const onAllComplete = useCallback((): void => {
    playTone(523, 0.15, 0.06);
    setTimeout(() => playTone(659, 0.15, 0.06), 100);
    setTimeout(() => playTone(784, 0.25, 0.08), 200);
    haptic([30, 50, 30, 50, 60]);
  }, [playTone, haptic]);

  const onTaskStarted = useCallback((): void => {
    playTone(660, 0.06, 0.04);
    haptic(15);
  }, [playTone, haptic]);

  return { onTaskComplete, onTaskFailed, onAllComplete, onTaskStarted };
}
