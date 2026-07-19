export const PROCESSING_CUE_INITIAL_DELAY_MS = 3_000;
export const PROCESSING_CUE_REPEAT_MS = 7_000;

export interface CueScheduler {
  setTimeout(callback: () => void, delayMs: number): number;
  clearTimeout(id: number): void;
}

const browserScheduler: CueScheduler = {
  setTimeout: (callback, delayMs) => window.setTimeout(callback, delayMs),
  clearTimeout: (id) => window.clearTimeout(id),
};

export class ProcessingCueController {
  private active = false;
  private timer?: number;

  constructor(
    private readonly play: () => void,
    private readonly silence: () => void = () => undefined,
    private readonly scheduler: CueScheduler = browserScheduler,
  ) {}

  begin(): void {
    if (this.active) return;
    this.active = true;
    this.schedule(PROCESSING_CUE_INITIAL_DELAY_MS);
  }

  stop(): void {
    this.active = false;
    if (this.timer !== undefined) this.scheduler.clearTimeout(this.timer);
    this.timer = undefined;
    this.silence();
  }

  private schedule(delayMs: number): void {
    this.timer = this.scheduler.setTimeout(() => {
      this.timer = undefined;
      if (!this.active) return;
      this.play();
      this.schedule(PROCESSING_CUE_REPEAT_MS);
    }, delayMs);
  }
}
