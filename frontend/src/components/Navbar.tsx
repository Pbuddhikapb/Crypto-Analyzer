import React, { useEffect, useState } from 'react';
import { Volume2, VolumeX, Activity, Radio, ShieldAlert } from 'lucide-react';
import { soundEngine } from '../state/audio';

interface NavbarProps {
  isConnected: boolean;
  latencyMs: number | null;
  activePositionsCount: number;
  maxPositions: number;
  isFuturesOnline: boolean;
  mode: 'SPOT' | 'FUTURES';
  macroBias?: string;
  onModeToggle: () => void;
}

export const Navbar: React.FC<NavbarProps> = ({
  isConnected,
  latencyMs,
  activePositionsCount,
  maxPositions,
  isFuturesOnline,
  mode,
  macroBias = 'NEUTRAL',
  onModeToggle,
}) => {
  const [isMuted, setIsMuted] = useState<boolean>(soundEngine.isMuted());
  const [currentTime, setCurrentTime] = useState<string>('');

  useEffect(() => {
    const updateClock = () => {
      const now = new Date();
      setCurrentTime(now.toTimeString().split(' ')[0] + ' UTC');
    };
    updateClock();
    const interval = setInterval(updateClock, 1000);
    return () => clearInterval(interval);
  }, []);

  const handleToggleSound = () => {
    const muted = soundEngine.toggleMute();
    setIsMuted(muted);
  };

  return (
    <header className="h-11 bg-dark-panel border-b border-dark-border px-4 flex items-center justify-between text-xs select-none">
      {/* Brand & Market Switcher */}
      <div className="flex items-center space-x-4">
        <div className="flex items-center space-x-2">
          <div className="w-2.5 h-2.5 rounded-sm bg-laser-cyan shadow-[0_0_8px_rgba(0,242,254,0.6)]" />
          <span className="font-bold tracking-wider text-slate-100 text-sm">
            CRYPTO<span className="text-laser-cyan">TERMINAL</span>
          </span>
          <span className="text-[10px] bg-dark-card border border-dark-border px-1.5 py-0.5 rounded text-slate-400 font-mono">
            v1.0-PRO
          </span>
        </div>

        <button
          onClick={onModeToggle}
          className="flex items-center space-x-1.5 px-2.5 py-1 bg-dark-card border border-dark-border hover:border-slate-600 rounded transition font-medium"
        >
          <span className={mode === 'FUTURES' ? 'text-purple-smc font-semibold' : 'text-slate-400'}>
            FUTURES
          </span>
          <span className="text-slate-600">/</span>
          <span className={mode === 'SPOT' ? 'text-laser-green font-semibold' : 'text-slate-400'}>
            SPOT
          </span>
        </button>

        {!isFuturesOnline && (
          <div className="flex items-center space-x-1 text-amber-warn bg-amber-warn/10 border border-amber-warn/30 px-2 py-0.5 rounded text-[11px]">
            <ShieldAlert size={12} />
            <span>Futures Prober: Spot Only Mode</span>
          </div>
        )}
      </div>

      {/* Center Macro Bias & Clock */}
      <div className="hidden md:flex items-center space-x-3 text-slate-400">
        {macroBias && (
          <div
            className={`flex items-center space-x-1 px-2 py-0.5 rounded text-[10px] font-bold border ${
              macroBias === 'BULLISH'
                ? 'bg-laser-green/15 text-laser-green border-laser-green/40 shadow-[0_0_8px_rgba(0,230,118,0.2)]'
                : macroBias === 'BEARISH'
                ? 'bg-crimson-red/15 text-crimson-red border-crimson-red/40 shadow-[0_0_8px_rgba(255,23,68,0.2)]'
                : 'bg-dark-card text-slate-400 border-dark-border'
            }`}
          >
            <span>MACRO BIAS: {macroBias}</span>
          </div>
        )}
        <div className="flex items-center space-x-1.5">
          <Activity size={13} className="text-laser-cyan" />
          <span className="font-mono">{currentTime}</span>
        </div>
      </div>

      {/* Right Telemetry Controls */}
      <div className="flex items-center space-x-3">
        {/* Latency */}
        <div className="flex items-center space-x-1 text-slate-400">
          <Radio size={12} className={isConnected ? 'text-laser-green' : 'text-crimson-red'} />
          <span>{latencyMs !== null ? `${latencyMs}ms` : '-- ms'}</span>
        </div>

        {/* Connection Status Badge */}
        <div className="flex items-center space-x-1.5 bg-dark-card border border-dark-border px-2 py-0.5 rounded">
          <span
            className={`w-2 h-2 rounded-full ${
              isConnected
                ? 'bg-laser-green shadow-[0_0_6px_rgba(0,230,118,0.8)]'
                : 'bg-crimson-red shadow-[0_0_6px_rgba(255,23,68,0.8)] animate-pulse'
            }`}
          />
          <span className={isConnected ? 'text-slate-200' : 'text-crimson-red'}>
            {isConnected ? 'LIVE' : 'OFFLINE'}
          </span>
        </div>

        {/* Active Trades Counter */}
        <div className="bg-dark-card border border-dark-border px-2 py-0.5 rounded text-slate-300">
          <span>POSITIONS: </span>
          <span className={activePositionsCount > 0 ? 'text-laser-cyan font-bold' : 'text-slate-400'}>
            {activePositionsCount}/{maxPositions}
          </span>
        </div>

        {/* Sound Mute Toggle */}
        <button
          onClick={handleToggleSound}
          title={isMuted ? 'Sound Alerts Muted' : 'Sound Alerts Active'}
          className="p-1 rounded bg-dark-card border border-dark-border hover:border-slate-500 text-slate-300 transition"
        >
          {isMuted ? <VolumeX size={15} className="text-slate-500" /> : <Volume2 size={15} className="text-laser-cyan" />}
        </button>
      </div>
    </header>
  );
};
