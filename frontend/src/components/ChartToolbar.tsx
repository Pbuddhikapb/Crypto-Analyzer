import React from 'react';
import { Eye, EyeOff, Maximize2, Minimize2 } from 'lucide-react';

interface ChartToolbarProps {
  symbol: string;
  futuresSymbol: string;
  activeTimeframe: string;
  onTimeframeChange: (tf: string) => void;
  showEMAs: boolean;
  onToggleEMAs: () => void;
  showFVGs: boolean;
  onToggleFVGs: () => void;
  showRSI: boolean;
  onToggleRSI: () => void;
  isRightPanelCollapsed: boolean;
  onToggleRightPanel: () => void;
}

const TIMEFRAMES = ['1m', '5m', '15m', '1h', '4h', '1d'];

export const ChartToolbar: React.FC<ChartToolbarProps> = ({
  symbol,
  futuresSymbol,
  activeTimeframe,
  onTimeframeChange,
  showEMAs,
  onToggleEMAs,
  showFVGs,
  onToggleFVGs,
  showRSI,
  onToggleRSI,
  isRightPanelCollapsed,
  onToggleRightPanel,
}) => {
  return (
    <div className="h-8 bg-dark-panel border-b border-dark-border px-2 flex items-center justify-between text-xs select-none">
      {/* Symbol & Timeframe Selectors */}
      <div className="flex items-center space-x-3">
        <div className="flex items-center space-x-1.5 font-bold text-slate-100">
          <span className="text-sm">{symbol}</span>
          {futuresSymbol !== symbol && (
            <span className="text-[10px] text-purple-smc bg-purple-smc/10 border border-purple-smc/30 px-1 rounded">
              PERP: {futuresSymbol}
            </span>
          )}
        </div>

        <div className="h-4 w-px bg-dark-border" />

        {/* Timeframe Buttons */}
        <div className="flex items-center space-x-1">
          {TIMEFRAMES.map((tf) => (
            <button
              key={tf}
              onClick={() => onTimeframeChange(tf)}
              className={`px-2 py-0.5 rounded text-[11px] font-mono transition ${
                activeTimeframe === tf
                  ? 'bg-laser-cyan/15 text-laser-cyan border border-laser-cyan/40 font-bold'
                  : 'text-slate-400 hover:text-slate-200 hover:bg-dark-card'
              }`}
            >
              {tf}
            </button>
          ))}
        </div>
      </div>

      {/* Indicator De-Clutter Toggles & Layout Control */}
      <div className="flex items-center space-x-2">
        {/* EMAs Toggle */}
        <button
          onClick={onToggleEMAs}
          className={`flex items-center space-x-1 px-2 py-0.5 rounded border text-[11px] transition ${
            showEMAs
              ? 'bg-amber-warn/15 text-amber-warn border-amber-warn/40 font-semibold'
              : 'text-slate-500 border-dark-border hover:border-slate-600'
          }`}
        >
          {showEMAs ? <Eye size={12} /> : <EyeOff size={12} />}
          <span>EMAs</span>
        </button>

        {/* FVGs Toggle */}
        <button
          onClick={onToggleFVGs}
          className={`flex items-center space-x-1 px-2 py-0.5 rounded border text-[11px] transition ${
            showFVGs
              ? 'bg-purple-smc/15 text-purple-smc border-purple-smc/40 font-semibold'
              : 'text-slate-500 border-dark-border hover:border-slate-600'
          }`}
        >
          {showFVGs ? <Eye size={12} /> : <EyeOff size={12} />}
          <span>FVGs</span>
        </button>

        {/* RSI Toggle */}
        <button
          onClick={onToggleRSI}
          className={`flex items-center space-x-1 px-2 py-0.5 rounded border text-[11px] transition ${
            showRSI
              ? 'bg-laser-green/15 text-laser-green border-laser-green/40 font-semibold'
              : 'text-slate-500 border-dark-border hover:border-slate-600'
          }`}
        >
          {showRSI ? <Eye size={12} /> : <EyeOff size={12} />}
          <span>RSI</span>
        </button>

        <div className="h-4 w-px bg-dark-border" />

        {/* Expand / Collapse Right Panel */}
        <button
          onClick={onToggleRightPanel}
          title={isRightPanelCollapsed ? 'Show Order Flow Panel' : 'Expand Chart Full Width'}
          className="p-1 rounded bg-dark-card border border-dark-border hover:border-slate-500 text-slate-300 transition"
        >
          {isRightPanelCollapsed ? <Minimize2 size={13} /> : <Maximize2 size={13} />}
        </button>
      </div>
    </div>
  );
};
