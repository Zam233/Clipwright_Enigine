import { useRef, useState, useEffect, useCallback } from 'react';
import { Play, Pause } from 'lucide-react';
import { cn } from '@/lib/utils';

interface AudioPlayerProps {
  src: string;
  className?: string;
}

export function AudioPlayer({ src, className }: AudioPlayerProps) {
  const audioRef = useRef<HTMLAudioElement>(null);
  const [playing, setPlaying] = useState(false);
  const [progress, setProgress] = useState(0);
  const [duration, setDuration] = useState(0);

  useEffect(() => {
    setPlaying(false);
    setProgress(0);
    setDuration(0);
  }, [src]);

  const toggle = useCallback(() => {
    const el = audioRef.current;
    if (!el) return;
    if (playing) {
      el.pause();
    } else {
      el.play();
    }
  }, [playing]);

  const onTimeUpdate = () => {
    const el = audioRef.current;
    if (el) setProgress(el.currentTime);
  };

  const onLoadedMetadata = () => {
    const el = audioRef.current;
    if (el) setDuration(el.duration);
  };

  const seek = (e: React.MouseEvent<HTMLDivElement>) => {
    const el = audioRef.current;
    if (!el || !duration) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const ratio = (e.clientX - rect.left) / rect.width;
    el.currentTime = ratio * duration;
    setProgress(el.currentTime);
  };

  const fmt = (s: number) => {
    const m = Math.floor(s / 60);
    const sec = Math.floor(s % 60);
    return `${m}:${String(sec).padStart(2, '0')}`;
  };

  return (
    <div className={cn('flex items-center gap-2 h-9', className)}>
      <audio
        ref={audioRef}
        src={src}
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onEnded={() => { setPlaying(false); setProgress(0); }}
        onTimeUpdate={onTimeUpdate}
        onLoadedMetadata={onLoadedMetadata}
        preload="metadata"
      />
      <button
        onClick={toggle}
        className="w-7 h-7 rounded-cw-full bg-primary/10 hover:bg-primary/20 flex items-center justify-center
          text-primary transition-colors cursor-pointer shrink-0"
      >
        {playing ? <Pause className="w-3.5 h-3.5" /> : <Play className="w-3.5 h-3.5 ml-0.5" />}
      </button>
      <div
        className="flex-1 h-1.5 bg-surface-container-high rounded-cw-full overflow-hidden cursor-pointer group"
        onClick={seek}
      >
        <div
          className="h-full bg-primary rounded-cw-full transition-[width] duration-100"
          style={{ width: duration ? `${(progress / duration) * 100}%` : '0%' }}
        />
      </div>
      <span className="text-caption font-mono text-on-surface-variant w-16 text-right shrink-0">
        {fmt(progress)} / {fmt(duration)}
      </span>
    </div>
  );
}
