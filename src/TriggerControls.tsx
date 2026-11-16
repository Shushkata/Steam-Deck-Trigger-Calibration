import { PanelSectionRow, SliderField } from '@decky/ui';
import { CAUTION, TriggerControls, markerPosition } from './calibration';
const small = { fontSize: 12, lineHeight: 1.5 };

function References({ original, min, max }: { original: number | null; min: number; max: number }) {
  const markers = [{ name: 'Default', value: original, color: '#c5cbd1', top: 0 },
    { name: 'Caution', value: CAUTION, color: '#e5bf47', top: 11 }];
  return <div aria-label="Calibration reference markers" style={{ width:'100%', pointerEvents:'none' }}>
    <div style={{ position:'relative', height:23, margin:'0 2px' }}>
      {markers.map(marker=>{
        const position = marker.value === null ? null : markerPosition(marker.value,min,max);
        return position === null ? null : <span key={marker.name} data-reference={marker.name}
          title={`${marker.name}: ${marker.value}`} aria-label={`${marker.name}: ${marker.value}`}
          style={{ position:'absolute', left:`${position}%`, top:marker.top,
            width:2, height:11, backgroundColor:marker.color, transform:'translateX(-1px)' }} />;
      })}
    </div>
    <div style={{ ...small, display:'flex', justifyContent:'space-between', gap:8 }}>
      <span>Default: {original ?? 'Unavailable'}</span><span>Caution: {CAUTION}</span>
    </div>
  </div>;
}

export function Trigger({ label, control, fine, disabled, stale=false, onChange }: {
  label:string; control:TriggerControls; fine:boolean; disabled:boolean; stale?:boolean; onChange:(value:number)=>void;
}) {
  const {current,minimum,original,selected,range,error} = control;
  // Native Decky fields are direct PanelSectionRow children. Markers/text have
  // separate rows; they cannot overlap the slider's hit target or focus item.
  return <>
    <PanelSectionRow><div style={{ width:'100%', ...small }}>
      <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',gap:8}}>
        <strong>{label}</strong><strong aria-label={`Selected MAX: ${selected ?? 'Unavailable'}`}
          style={{fontSize:28,lineHeight:1.1,fontVariantNumeric:'tabular-nums'}}>{selected ?? '—'}</strong>
      </div>
      Selected MAX: {selected ?? 'Unavailable'} · {stale?'Last Read':'Current MAX'}: {current ?? 'Unavailable'}
    </div></PanelSectionRow>
    {error && <PanelSectionRow><div role="alert" style={small}>{error}</div></PanelSectionRow>}
    {range && selected !== null && <>
      {SliderField == null ? <PanelSectionRow><div role="alert" style={small}>
        Decky SliderField is unavailable in this Steam UI build. Update Decky Loader and reopen Calibration Settings.
      </div></PanelSectionRow> : <PanelSectionRow>
        <SliderField label={`Adjust ${label.startsWith('Left') ? 'LT' : 'RT'} MAX`} layout="below"
          value={selected} min={range.min} max={range.max} step={fine ? 1 : 20}
          validValues="range" minimumDpadGranularity={fine ? 1 : 20}
          showValue={false} editableValue={false} notchTicksVisible={false} disabled={disabled}
          onChange={(value:number)=>{
            if (Number.isFinite(value)) onChange(Math.max((minimum ?? 0)+1,Math.min(range.max,Math.max(range.min,Math.round(value)))));
          }} />
      </PanelSectionRow>}
      <PanelSectionRow><References original={original} min={range.min} max={range.max}/></PanelSectionRow>
    </>}
    {!range && <PanelSectionRow><div style={small}>
      Default: {original ?? 'Unavailable'} · Caution: {CAUTION}
    </div></PanelSectionRow>}
  </>;
}
