import React from 'react';
import {AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';

const stations = [
  {x: 92, y: 170, label: 'SURFACE', detail: 'raw context', color: '#d9f99d', start: 10},
  {x: 270, y: 94, label: 'FRONT DOOR', detail: 'OIDC', color: '#f4b860', start: 58},
  {x: 470, y: 170, label: 'TUNNEL', detail: 'Tailnet :8081', color: '#66d6cf', start: 106},
  {x: 660, y: 94, label: 'INSTANCE', detail: 'local authority', color: '#b8b5ff', start: 154},
  {x: 850, y: 170, label: 'STAGING', detail: 'V2Capture', color: '#efad67', start: 202},
  {x: 660, y: 300, label: 'MINER', detail: 'planned', color: '#e87951', start: 250},
  {x: 850, y: 300, label: 'GRAPH', detail: 'evidence', color: '#d9f99d', start: 298},
];

const links = [[0, 1], [1, 2], [2, 3], [3, 4], [4, 5], [5, 6]];

const line = (a, b) => {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const length = Math.sqrt(dx * dx + dy * dy);
  const angle = Math.atan2(dy, dx) * (180 / Math.PI);
  return {length, angle};
};

export const SystemMap = () => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const cycle = frame % 360;
  const titleIn = spring({frame, fps, config: {damping: 18, stiffness: 80}});

  return (
    <AbsoluteFill style={{background: '#0b0e0d', color: '#eee7d3', overflow: 'hidden', fontFamily: 'IBM Plex Mono, monospace'}}>
      <div style={{position: 'absolute', inset: 0, opacity: 0.14, backgroundImage: 'linear-gradient(rgba(217,249,157,.22) 1px, transparent 1px),linear-gradient(90deg,rgba(217,249,157,.22) 1px,transparent 1px)', backgroundSize: '42px 42px'}} />
      <div style={{position: 'absolute', inset: '-30%', background: `radial-gradient(circle at ${25 + cycle / 7}% 40%, rgba(102,214,207,.13), transparent 27%), radial-gradient(circle at 72% 70%, rgba(232,121,81,.12), transparent 30%)`}} />

      <div style={{position: 'absolute', left: 48, top: 34, opacity: titleIn, transform: `translateY(${interpolate(titleIn, [0, 1], [18, 0])}px)`}}>
        <div style={{fontSize: 13, letterSpacing: 3.8, color: '#d9f99d'}}>SIGNAL PATH / V2</div>
        <div style={{fontFamily: 'Unbounded, sans-serif', fontWeight: 600, fontSize: 25, marginTop: 8}}>Context becomes knowledge here.</div>
      </div>

      {links.map(([from, to], index) => {
        const a = stations[from];
        const b = stations[to];
        const {length, angle} = line(a, b);
        const reveal = interpolate(frame, [a.start + 20, b.start], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
        return (
          <div key={`${from}-${to}`} style={{position: 'absolute', left: a.x, top: a.y, width: length * reveal, height: 2, transformOrigin: 'left center', transform: `rotate(${angle}deg)`, background: `linear-gradient(90deg, ${a.color}, ${b.color})`, opacity: .55}}>
            <div style={{position: 'absolute', right: -3, top: -2, width: 6, height: 6, borderRadius: '50%', background: b.color, boxShadow: `0 0 14px ${b.color}`}} />
          </div>
        );
      })}

      {stations.map((station, index) => {
        const enter = spring({frame: frame - station.start, fps, config: {damping: 14, stiffness: 110}});
        const active = cycle >= station.start % 360 && cycle < (station.start + 52) % 360;
        return (
          <div key={station.label} style={{position: 'absolute', left: station.x, top: station.y, opacity: enter, transform: `translate(-50%, -50%) scale(${interpolate(enter, [0, 1], [.65, 1])})`}}>
            <div style={{width: 18, height: 18, transform: 'rotate(45deg)', background: active ? station.color : '#101412', border: `2px solid ${station.color}`, boxShadow: active ? `0 0 0 8px ${station.color}18, 0 0 24px ${station.color}` : `0 0 0 5px ${station.color}0b`, transition: 'box-shadow .2s'}} />
            <div style={{position: 'absolute', width: 150, left: -66, top: 27, textAlign: 'center'}}>
              <div style={{fontSize: 12, letterSpacing: 2.2, color: station.color}}>{station.label}</div>
              <div style={{fontFamily: 'Newsreader, serif', fontStyle: 'italic', marginTop: 3, fontSize: 15, color: '#a9a79d'}}>{station.detail}</div>
            </div>
            <div style={{position: 'absolute', left: -78, top: -82, fontSize: 10, color: '#555d57'}}>0{index + 1}</div>
          </div>
        );
      })}

      <div style={{position: 'absolute', left: 48, right: 48, bottom: 28, display: 'flex', justifyContent: 'space-between', fontSize: 10, letterSpacing: 2, color: '#68716a'}}>
        <span>PUBLIC EDGE</span><span>ENCRYPTED PASSAGE</span><span>LOCAL WORLD</span>
      </div>
    </AbsoluteFill>
  );
};
