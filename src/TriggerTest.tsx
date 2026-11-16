import { ButtonItem, PanelSection, PanelSectionRow } from '@decky/ui';
import { callable } from '@decky/api';
import { useEffect, useState } from 'react';

export type InputState = { ok:boolean; token?:string; active?:boolean; error?:string|null;
  values?:number[]|null; minimum?:(number|null)[]; maximum?:(number|null)[]; percent?:number[]|null;
  stale?:boolean; samples?:number; semantics?:string; report_range_reached?:boolean[];
  returned_to_report_zero?:boolean[]; full_analog_travel?:string;
  idle?:{state:string;remaining:number;result:string|null;maximum:number[];minimum:(number|null)[];samples:number;reason:string;nonzero_samples?:number[]}|null };
const start = callable<[],InputState>('start_input');
const state = callable<[token:string],InputState>('input_state');
const stop = callable<[token:string],{ok:boolean}>('stop_input');
const idle = callable<[token:string],InputState>('test_idle_input');
const reset = callable<[token:string],InputState>('reset_input_test');
const small = {fontSize:12,lineHeight:1.4};

export function InputBars({input}:{input:InputState|null}) {
  return <>{['Left Trigger (LT)','Right Trigger (RT)'].map((label,i)=>
    <PanelSectionRow key={label}><div style={{width:'100%'}}>
      <div>{label}: <strong>{input?.percent?.[i]?.toFixed(2) ?? '—'}%</strong></div>
      <div role="progressbar" aria-label={`${label} input`} aria-valuemin={0} aria-valuemax={100}
        aria-valuenow={input?.percent?.[i]} style={{height:8,border:'1px solid #7c858d',margin:'6px 0'}}>
        <div style={{height:'100%',width:`${input?.percent?.[i] ?? 0}%`,backgroundColor:'#c5cbd1',transition:'width 50ms linear'}} />
      </div>
    </div></PanelSectionRow>)}</>;
}

export function TriggerTest() {
  const [active,setActive] = useState(false);
  const [input,setInput] = useState<InputState|null>(null);
  const [message,setMessage] = useState('');
  useEffect(()=>{
    if (!active) return;
    let cancelled = false;
    let token:string|undefined;
    let timer:number|undefined;
    async function poll() {
      if (cancelled || !token) return;
      try {
        const next = await state(token);
        if (cancelled) return;
        setInput(next);
        if (!next.ok || next.error || !next.active) {
          setMessage(next.error || 'Input reader stopped. Close and reopen Trigger Test.');
          return;
        }
        setMessage(next.stale ? 'Waiting for input…' : '');
        timer = window.setTimeout(()=>void poll(),50);
      } catch(error) { if (!cancelled) setMessage(`Input reader error: ${String(error)}`); }
    }
    void start().then(next=>{
      token = next.token;
      if (cancelled) { if (token) void stop(token); return; }
      setInput(next);
      if (!next.ok || !token) { setMessage(next.error || 'Input reader unavailable.'); return; }
      void poll();
    }).catch(error=>{if (!cancelled) setMessage(String(error));});
    return ()=>{
      cancelled = true;
      window.clearTimeout(timer);
      if (token) void stop(token).catch(()=>{});
    };
  },[active]);

  async function measure(operation:(token:string)=>Promise<InputState>) {
    if (!input?.token) return;
    try { setInput(await operation(input.token)); }
    catch(error) { setMessage(String(error)); }
  }

  return <PanelSection title="Live Trigger Test">
    <PanelSectionRow><ButtonItem layout="below" onClick={()=>{setMessage('');setActive(!active);}}>
      {active?'Close Trigger Test':'Open Trigger Test'}</ButtonItem></PanelSectionRow>
    {active && <>
      <InputBars input={input} />
      {message && <PanelSectionRow><div style={small} role="status">{message.split('\n')[0].slice(0,160)}</div></PanelSectionRow>}
      <PanelSectionRow><ButtonItem layout="below" disabled={!input?.active || input?.idle?.state==='measuring'}
        onClick={()=>void measure(idle)}>Test Idle Input</ButtonItem></PanelSectionRow>
      {input?.idle && <PanelSectionRow><div style={small}>
        {input.idle.state==='measuring' ? `Release both triggers and do not touch them. ${Math.ceil(input.idle.remaining)}s remaining.` :
          `Idle Input: ${input.idle.result ?? 'Inconclusive'}.`}
        <br />LT maximum: {input.idle.maximum[0]} · RT maximum: {input.idle.maximum[1]}
      </div></PanelSectionRow>}
      <PanelSectionRow><ButtonItem layout="below" disabled={!input?.active}
        onClick={()=>void measure(reset)}>Reset Test</ButtonItem></PanelSectionRow>
    </>}
  </PanelSection>;
}
