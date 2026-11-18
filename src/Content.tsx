import { ButtonItem, ConfirmModal, PanelSection, PanelSectionRow, ToggleField, showModal } from '@decky/ui';
import { callable } from '@decky/api';
import { useEffect, useRef, useState } from 'react';
import { Values, validProposal, triggerControls } from './calibration';
import { Trigger } from './TriggerControls';
import { TriggerTest } from './TriggerTest';

type Record = {values:Values;confirmed_at?:number;provenance?:string;suspected_modified?:boolean};
export type Status = {ok:boolean;error?:string|null;message?:string;current?:Values|null;initial?:Record|null;
  current_is_stale?:boolean;refresh_warning?:string|null;
  selected?:{lmax:number;rmax:number}|null;selected_matches_current?:boolean;
  current_matches_original?:boolean|null;
  original_backup?:{source?:string;valid_copies?:number;errors?:{[name:string]:string};primary?:string;reserve?:string};
  profiles?:{lkg:Record|null};windows?:{l:{min:number;max:number};r:{min:number;max:number}}|null;
  conflicts?:{blockers:string[];warnings:string[]}|null;manual_apply_enabled?:boolean;can_set_current_as_default?:boolean;
  apply_blocked_reason?:string|null;configuration_repairs?:{file:string;preserved:string}[];working_confirmation_available?:boolean;last_apply?:{status:string;error?:string}|null};
const readCalibration = callable<[],Status>('read_calibration');
const getStatus = callable<[],Status>('get_status');
const exportOriginal = callable<[],{ok:boolean;path?:string;message?:string;error?:string}>('export_original_backup');
const setCurrentAsDefault = callable<[confirmed:boolean],Status>('set_current_as_default');
const selectValues = callable<[lmax:number,rmax:number],Status>('select_values');
const applySelected = callable<[expected:Values,lmax:number,rmax:number,confirmed:boolean],Status>('apply_selected');
const restoreDefault = callable<[confirmed:boolean],Status>('restore_default');
const restoreLastGood = callable<[confirmed:boolean],Status>('restore_last_good');
const confirmWorking = callable<[expected:Values,confirmed:boolean],Status>('confirm_working');
const exportDiagnostics = callable<[],{ok:boolean;path?:string;error?:string}>('export_diagnostics');
const small = {fontSize:12,lineHeight:1.4};

// One coalescing preference queue survives panel remounts. A newly opened
// panel waits for the previous panel's last selection, preventing an older
// in-flight save from overwriting a newer choice.
let pendingSelection:{left:number;right:number;done:(state:Status)=>void;failed:(error:unknown)=>void}|null=null;
let preferenceWork:Promise<void>|null=null;
function drainSelections() {
  if(preferenceWork) return;
  preferenceWork=(async()=>{
    while(pendingSelection) {
      const item=pendingSelection;
      pendingSelection=null;
      try { item.done(await selectValues(item.left,item.right)); }
      catch(error) { item.failed(error); }
    }
  })().finally(()=>{preferenceWork=null;if(pendingSelection)drainSelections();});
}
async function waitForSelections() { while(preferenceWork) await preferenceWork; }

// Steam may unmount the QAM content while its confirmation modal is open.
// Retain only that handoff, then consume the existing RPC reply in the new view.
// Ordinary panel closes still start at Read; no polling or hardware read is added.
type ConfirmationHandoff = {status:Status|null;hasRead:boolean;left:number;right:number;fine:boolean;confirmed:boolean;result:Promise<Status>};
let confirmationHandoff:ConfirmationHandoff|null=null;

export function Content() {
  const resume=confirmationHandoff;
  const [status,setStatus] = useState<Status|null>(resume?.status ?? null);
  const [hasRead,setHasRead] = useState(resume?.hasRead ?? false);
  const [attempted,setAttempted] = useState(!!resume);
  const [left,setLeft] = useState(resume?.left ?? 0);
  const [right,setRight] = useState(resume?.right ?? 0);
  const [busy,setBusy] = useState(!!resume);
  const [fine,setFine] = useState(resume?.fine ?? false);
  const [diagnostics,setDiagnostics] = useState(false);
  const [saving,setSaving] = useState(false);
  const [message,setMessage] = useState('');
  const mounted = useRef(true);
  const revision = useRef(0);
  const desired = useRef({left:resume?.left ?? 0,right:resume?.right ?? 0});
  const lastStatus = useRef<Status|null>(resume?.status ?? null);
  const busyNow = useRef(!!resume);
  const request = useRef(0);

  function accept(next:Status,synchronize=true) {
    const previous=lastStatus.current;
    const retained=next.current==null && previous?.current!=null;
    next={...previous,...next,current:next.current ?? previous?.current,
      initial:next.initial ?? previous?.initial,selected:next.selected ?? previous?.selected,
      current_is_stale:next.current_is_stale || retained,
      manual_apply_enabled:next.manual_apply_enabled===true && !retained};
    lastStatus.current=next;
    setStatus(next);
    if (synchronize) {
      const left = triggerControls(next.current,next.initial?.values,next.selected?.lmax ?? next.current?.lmax ?? NaN,'l',next.selected?.rmax);
      const right = triggerControls(next.current,next.initial?.values,next.selected?.rmax ?? next.current?.rmax ?? NaN,'r',next.selected?.lmax);
      if (left.selected !== null) { desired.current.left=left.selected; setLeft(left.selected); }
      if (right.selected !== null) { desired.current.right=right.selected; setRight(right.selected); }
    }
    setMessage(next.error || next.message || '');
  }

  useEffect(()=>{
    mounted.current = true;
    const ticket=++request.current;
    void waitForSelections().then(()=>resume ? resume.result : getStatus()).then(next=>{
      if(mounted.current && ticket===request.current) {
        accept(next);
        if(resume) {
          setHasRead(resume.hasRead || !!(resume.confirmed && next.ok && validProposal(next.current,next.current?.lmax ?? NaN,next.current?.rmax ?? NaN)));
          busyNow.current=false;setBusy(false);
          if(confirmationHandoff===resume) confirmationHandoff=null;
        }
      }
    })
      .catch(error=>{if(mounted.current && ticket===request.current) {
        const message=`Calibration read failed: ${String(error)}`;
        accept({ok:false,error:message});
      }});
    return ()=>{mounted.current=false;++request.current;};
  },[]);

  function change(side:'l'|'r',value:number) {
    if(busyNow.current) return;
    const next = {...desired.current,[side==='l'?'left':'right']:value};
    desired.current=next;
    const {left:nextLeft,right:nextRight}=next;
    setLeft(nextLeft);setRight(nextRight);setSaving(true);
    if(leftControl.current === null || rightControl.current === null) {
      setSaving(false);setMessage('Selection changed locally. Both valid trigger readings are required to save preferences.');
      return;
    }
    const version=++revision.current;
    pendingSelection={left:nextLeft,right:nextRight,done:next=>{
      if(mounted.current && version===revision.current) {
        accept(next,false);setSaving(false);
        setMessage(next.ok ? '' : next.error || 'Unable to save selection.');
      }
    },failed:error=>{
      if(mounted.current && version===revision.current) {setSaving(false);setMessage(String(error));}
    }};
    // One serialized preference writer, coalescing drag events. It never calls
    // Apply, and finishes its last pending save even if the panel is closed.
    drainSelections();
  }

  async function run(operation:()=>Promise<Status>,markRead=false) {
    if (busyNow.current) return;
    busyNow.current=true;
    if(mounted.current) setAttempted(true);
    const ticket=++request.current;
    if(mounted.current) setBusy(true);
    try {
      await waitForSelections();
      let next=await operation();
      if(next.current==null && lastStatus.current?.current) next={...next,
        error:next.error || 'Calibration verification pending. Use Read Calibration.',
        current_is_stale:true,manual_apply_enabled:false};
      const fresh=next.current && !next.current_is_stale && validProposal(next.current,next.current.lmax,next.current.rmax);
      if(markRead && !fresh && !next.error) next={...next,error:'Calibration read did not return valid current MAX/MIN values. Press Read Calibration to retry.'};
      if(mounted.current && ticket===request.current) {
        accept(next);
        if(markRead && fresh) setHasRead(true);
      }
      return next;
    }
    catch {
      const failed:Status={ok:false,error:'Calibration verification pending. Use Read Calibration.',
        current_is_stale:true,manual_apply_enabled:false};
      if(mounted.current && ticket===request.current) accept(failed);
      return failed;
    }
    finally { busyNow.current=false;if(mounted.current)setBusy(false); }
  }

  function confirm(title:string,description:string,action:()=>Promise<Status>,button:string) {
    let resolve!:(state:Status)=>void,started=false;
    const snapshot=lastStatus.current && hasRead ? {...lastStatus.current,
      selected:{lmax:desired.current.left,rmax:desired.current.right}} : lastStatus.current;
    const handoff:ConfirmationHandoff={status:snapshot,hasRead,left:desired.current.left,right:desired.current.right,fine,confirmed:false,
      result:new Promise<Status>(done=>{resolve=done;})};
    confirmationHandoff=handoff;
    const finish=(state:Status)=>{
      resolve(state);
      if(mounted.current && confirmationHandoff===handoff) confirmationHandoff=null;
    };
    const cancel=()=>{if(!started)finish(snapshot ?? {ok:false});};
    const modal=showModal(<ConfirmModal strTitle={title} strDescription={description} strOKButtonText={button}
      strCancelButtonText="Cancel" onCancel={cancel} onOK={()=>{
        if(started)return;
        started=true;handoff.confirmed=true;
        void run(action).then(next=>finish(next ?? {...snapshot,ok:false,manual_apply_enabled:false}));
      }} />);
    void modal?.ClosedPromise?.then(cancel,cancel);
  }

  const current = status?.current;
  const original = status?.initial?.values;
  const leftControl = triggerControls(current,original,left,'l',right);
  const rightControl = triggerControls(current,original,right,'r',left);
  const lkg = status?.profiles?.lkg;
  const valid = validProposal(current,left,right);
  const brief = (text:string) => text.split('\n')[0].slice(0,180);

  const restoreOriginal = () => confirm('Restore Original?',
    'Restore all archived LT/RT MAX and MIN values from the protected Original Calibration backup and verify read-back. This is not factory recalibration.',
    ()=>restoreDefault(true),'Restore Original');

  if(!hasRead) return <PanelSection title="Trigger Calibration">
    <PanelSectionRow><div style={small}>Read the current calibration to begin.</div></PanelSectionRow>
    <PanelSectionRow><ButtonItem layout="below" disabled={busy || saving}
      onClick={()=>void run(readCalibration,true)}>Read Calibration</ButtonItem></PanelSectionRow>
    {attempted && message && <PanelSectionRow><div role={status?.error?'alert':'status'} style={small}>{brief(message)}</div></PanelSectionRow>}
    {original && <PanelSectionRow><ButtonItem layout="below" disabled={busy}
      onClick={restoreOriginal}>Restore Original</ButtonItem></PanelSectionRow>}
    {original && attempted && <PanelSectionRow><ButtonItem layout="below" disabled={busy} onClick={()=>{
      void exportOriginal().then(result=>setMessage(result.ok ? `Original Backup exported: ${result.path}. Copy this JSON to another device.` : result.error || 'Export failed.'))
        .catch(error=>setMessage(String(error)));
    }}>Export Original Backup</ButtonItem></PanelSectionRow>}
  </PanelSection>;

  return <>
    <PanelSection title="Calibration Settings">
      <PanelSectionRow><ButtonItem layout="below" disabled={busy || saving}
        onClick={()=>void run(readCalibration,true)}>Read Calibration</ButtonItem></PanelSectionRow>
      {status?.can_set_current_as_default && !original && <PanelSectionRow><ButtonItem layout="below" disabled={busy || saving}
        onClick={()=>confirm('Save Original Calibration?',
          'Confirm you fully shut down the Deck and turned it back on before this setup. Read and save the current hardware values once as Original Calibration? '+
          'These values may already be modified and are not guaranteed factory calibration. The locked backups cannot be replaced here.',
          ()=>setCurrentAsDefault(true),'Confirm Cold Start & Save Original')}>Save Original Calibration</ButtonItem></PanelSectionRow>}
      {message && <PanelSectionRow><div role={status?.error ? 'alert':'status'} style={small}>{brief(message)}</div></PanelSectionRow>}
      {original && <PanelSectionRow><div style={small}>
        <strong>🔒 Original Calibration</strong><br />LT MAX: {original.lmax} · RT MAX: {original.rmax}<br />
        LT MIN: {original.lmin} · RT MIN: {original.rmin}
        {status?.current_matches_original!==undefined && status.current_matches_original!==null && <><br />
          {status.current_is_stale?'Last Read':'Current'}: {status.current_matches_original?'matches Original':'differs from Original'}
        </>}
      </div></PanelSectionRow>}
      {original && status?.original_backup?.valid_copies===1 && <PanelSectionRow><div style={small} role="alert">
        {status.original_backup.source==='initial-reserve.json'?'Using verified recovery backup. Primary file preserved.':'Only one valid Original backup remains. Export it and preserve both files.'}
      </div></PanelSectionRow>}
      {original && <PanelSectionRow><ButtonItem layout="below" disabled={busy || saving} onClick={()=>{
        void exportOriginal().then(result=>setMessage(result.ok ? `Original Backup exported: ${result.path}. Copy this JSON to another device.` : result.error || 'Export failed.'))
          .catch(error=>setMessage(String(error)));
      }}>Export Original Backup</ButtonItem></PanelSectionRow>}
      <PanelSectionRow><ToggleField label="Fine Adjustment (1)" checked={fine} disabled={busy} onChange={setFine}/></PanelSectionRow>
      <Trigger label="Left Trigger (LT)" control={leftControl} fine={fine} disabled={busy} stale={status?.current_is_stale}
        onChange={value=>change('l',value)} />
      <Trigger label="Right Trigger (RT)" control={rightControl} fine={fine} disabled={busy} stale={status?.current_is_stale}
        onChange={value=>change('r',value)} />
      <PanelSectionRow><ButtonItem layout="below" disabled={busy || saving || !valid || !status?.manual_apply_enabled}
        onClick={()=>current && confirm('Apply calibration?',
          `Write LT ${left} / RT ${right}? MIN and negative-range values stay unchanged. `+
          'No automatic rollback. Use Restore Original if needed.',
          ()=>applySelected(current,left,right,true),'Apply')}>Apply</ButtonItem></PanelSectionRow>
      {status?.apply_blocked_reason && status.apply_blocked_reason!==message && <PanelSectionRow><div style={small} role="alert">{brief(status.apply_blocked_reason)}</div></PanelSectionRow>}
      <PanelSectionRow><ButtonItem layout="below" disabled={busy}
        onClick={restoreOriginal}>Restore Original</ButtonItem></PanelSectionRow>
    </PanelSection>

    <TriggerTest />

    <PanelSection title="Diagnostics">
      <PanelSectionRow><ButtonItem layout="below" onClick={()=>setDiagnostics(!diagnostics)}>
        {diagnostics?'Hide Diagnostics':'Show Diagnostics'}</ButtonItem></PanelSectionRow>
      {diagnostics && <>
        <PanelSectionRow><div style={small}>
          {status?.current_is_stale?'Last Read Calibration':'Current Calibration'}: LT {leftControl.current ?? 'Unavailable'} / RT {rightControl.current ?? 'Unavailable'}<br />
          Selected Values: LT {leftControl.selected ?? 'Unavailable'} / RT {rightControl.selected ?? 'Unavailable'}<br />
          MIN: LT {current?.lmin ?? '—'} / RT {current?.rmin ?? '—'}<br />
          Apply status: {status?.last_apply?.status ?? 'No recorded write'}<br />
          {status?.error}<br />
          Original MIN: LT {original?.lmin ?? '—'} / RT {original?.rmin ?? '—'}
        </div></PanelSectionRow>
        {status?.original_backup && <PanelSectionRow><div style={{...small,overflowWrap:'anywhere'}}>
          Original backup: {status.original_backup.primary}<br />
          Recovery backup: {status.original_backup.reserve}
        </div></PanelSectionRow>}
        {status?.conflicts && [...status.conflicts.blockers,...status.conflicts.warnings].map(text=>
          <PanelSectionRow key={text}><div style={small}>{text}</div></PanelSectionRow>)}
        {status?.configuration_repairs?.map(row=><PanelSectionRow key={row.preserved}><div style={small}>
          {row.file}: reset; original saved as {row.preserved}.
        </div></PanelSectionRow>)}
        {status?.working_confirmation_available && current && <PanelSectionRow><ButtonItem layout="below" disabled={busy || saving}
          onClick={()=>confirm('Save tested calibration?', 'Both triggers work correctly: idle, press, full range and release.',
            ()=>confirmWorking(current,true),'Confirm Working')}>Confirm Working</ButtonItem></PanelSectionRow>}
        {lkg && <PanelSectionRow><ButtonItem layout="below" disabled={busy || saving}
          onClick={()=>confirm('Restore Last Known Good?', 'Write the last explicitly confirmed calibration and verify read-back.',
            ()=>restoreLastGood(true),'Restore Last Known Good')}>Restore Last Known Good</ButtonItem></PanelSectionRow>}
        <PanelSectionRow><ButtonItem layout="below" disabled={busy} onClick={()=>{
          void exportDiagnostics().then(result=>setMessage(result.ok ? `Diagnostics exported: ${result.path}` : result.error || 'Export failed.'))
            .catch(error=>setMessage(String(error)));
        }}>Export Diagnostics</ButtonItem></PanelSectionRow>
      </>}
    </PanelSection>
  </>;
}
