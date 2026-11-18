// Compiled render/RPC tests with stubbed Steam/React globals. No native
// SteamUI layout, gamepad, touchscreen, HID or physical-write claim is implied.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const source=readFileSync(new URL('../dist/index.js',import.meta.url),'utf8');
const current={lmax:1900,lmin:518,rmax:1850,rmin:506,lneg:12,rneg:13};
const defaultState={ok:true,current,initial:{values:current,provenance:'Initial Calibration'},
  selected:{lmax:1900,rmax:1850},profiles:{lkg:null},
  windows:{l:{min:1820,max:1920},r:{min:1820,max:1900}},manual_apply_enabled:true};

function harness(initial=defaultState,nativeSliderAvailable=true,respond) {
  let backend=structuredClone(initial);
  let states=[],refs=[],effectStates=[],generation=0;
  let hook=0,ref=0,effect=0,nodes=[],effects=[];
  const calls=[],modals=[],modalClosures=[];
  const element=(type,props)=>({type,props:props??{}});
  const input={ok:true,token:'input-session',active:true,values:[0,32767],percent:[0,100],samples:5,stale:false,
    minimum:[0,0],maximum:[0,32767],report_range_reached:[false,true],returned_to_report_zero:[false,false]};
  const sandbox={
    window:{setTimeout:()=>1,clearTimeout:()=>{},
      __DECKY_SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED_deckyLoaderAPIInit:{connect:()=>({
        _version:2,callable:route=>async(...args)=>{
          calls.push({route,args});
          if(respond) {const response=await respond(route,args);if(response!==undefined)return response;}
          if(route==='select_values') backend={...backend,selected:{lmax:args[0],rmax:args[1]}};
          if(['start_input','input_state'].includes(route)) return input;
          if(route==='test_idle_input') return {...input,idle:{state:'complete',result:'Inconclusive',maximum:[2,0],samples:100,remaining:0,reason:'Unverified threshold'}};
          if(route==='export_diagnostics') return {ok:true,path:'/downloads/diagnostics.zip'};
          return backend;
        }})}},
    SP_JSX:{jsx:element,jsxs:element,Fragment:'Fragment'},
    SP_REACT:{
      useState:initial=>{const i=hook++,owner=generation,owned=states;if(!(i in owned))owned[i]=initial;return[owned[i],value=>{if(owner===generation)owned[i]=value;}];},
      useRef:initial=>{const i=ref++;return refs[i]??(refs[i]={current:initial});},
      useEffect:(fn,deps)=>{const i=effect++;const old=effectStates[i];
        if(!old||deps.some((v,n)=>v!==old.deps[n])) {effects.push(()=>{old?.cleanup?.();effectStates[i]={deps,cleanup:fn()};});}}
    },
    DFL:{ButtonItem:'ButtonItem',ConfirmModal:'ConfirmModal',PanelSection:'PanelSection',PanelSectionRow:'PanelSectionRow',
      SliderField:nativeSliderAvailable?'SliderField':undefined,ToggleField:'ToggleField',staticClasses:{Title:'Title'},showModal:modal=>{
        modals.push(modal);return {ClosedPromise:new Promise(resolve=>modalClosures.push(resolve))};}},
    console,Date
  };
  vm.createContext(sandbox);
  vm.runInContext(source.replace(/export\s*\{\s*index as default\s*\};/,'globalThis.pluginFactory=index;'),sandbox);
  const plugin=sandbox.pluginFactory();
  function visit(node) {
    if(Array.isArray(node)){node.forEach(visit);return;}
    if(!node||typeof node!=='object')return;
    if(typeof node.type==='function'){visit(node.type(node.props));return;}
    nodes.push(node);visit(node.props.children);
  }
  function render(){hook=ref=effect=0;nodes=[];visit(plugin.content);return nodes;}
  const tick=()=>new Promise(resolve=>setImmediate(resolve));
  async function mount(begin=true){render();effects.splice(0).forEach(fn=>fn());await tick();render();
    if(begin){button('Read Calibration').props.onClick();await tick();render();}}
  function button(label){return nodes.find(n=>n.type==='ButtonItem'&&n.props.children===label);}
  function text(node){if(Array.isArray(node))return node.map(text).join('');if(node&&typeof node==='object')return text(node.props?.children);return node==null||typeof node==='boolean'?'':String(node);}
  function destroy(){effectStates.forEach(e=>e.cleanup?.());generation++;states=[];refs=[];effectStates=[];effects=[];}
  async function remount(){destroy();await mount(false);}
  return {plugin,calls,modals,mount,remount,render,tick,button,get nodes(){return nodes;},
    runEffects:()=>effects.splice(0).forEach(fn=>fn()),text:()=>nodes.map(n=>text(n)).join(' '),
    closeModal:()=>modalClosures.at(-1)?.(),destroy};
}

test('Live calibration RPC reaches rendered numerical values and saved preset',async()=>{
  const h=harness({...defaultState,selected:{lmax:1910,rmax:1870}});await h.mount();
  assert.equal(h.calls[0].route,'get_status');
  assert.equal(h.calls[1].route,'read_calibration');
  const sliders=h.nodes.filter(n=>n.type==='SliderField');
  assert.equal(sliders[0].props.value,1910);assert.equal(sliders[1].props.value,1870);
  assert.match(h.text(),/Current MAX: 1900/);assert.match(h.text(),/Current MAX: 1850/);
  assert.match(h.text(),/Selected MAX: 1910/);assert.match(h.text(),/Selected MAX: 1870/);
  h.destroy();
});

test('A partial failed Apply response retains both sliders, Selected, Default and Last Read',async()=>{
  const h=harness(defaultState,true,route=>route==='apply_selected'?{
    ok:false,error:'Temporary HID read error',current:null,initial:null,selected:null,
    read_status:'Error',verification_pending:true,manual_apply_enabled:false}:undefined);
  await h.mount();
  h.nodes.find(n=>n.type==='SliderField').props.onChange(1890);
  await h.tick();h.render();
  h.button('Apply').props.onClick();h.modals[0].props.onOK();await h.tick();h.render();
  const sliders=h.nodes.filter(n=>n.type==='SliderField');
  assert.equal(sliders.length,2);assert.equal(sliders[0].props.value,1890);
  assert.match(h.text(),/Last Read: 1900/);assert.match(h.text(),/Default: 1900/);
  assert.match(h.text(),/Temporary HID read error/);
  assert.equal(h.button('Apply').props.disabled,true);
  assert.doesNotMatch(h.text(),/Unavailable/);h.destroy();
});

test('An older initial read cannot overwrite a later completed explicit Read',async()=>{
  let release,count=0;
  const delayed=new Promise(resolve=>release=resolve);
  const newer={...defaultState,current:{...current,lmax:1910},state_revision:2};
  const h=harness(defaultState,true,route=>{
    if(route==='get_status')return delayed;
    if(route==='read_calibration')return newer;
  });
  await h.mount(false);
  h.button('Read Calibration').props.onClick();await h.tick();h.render();
  release({...defaultState,current:null,ok:false,read_status:'Error',state_revision:1});
  await h.tick();h.render();
  assert.equal(h.nodes.filter(n=>n.type==='SliderField').length,2);
  assert.match(h.text(),/Current MAX: 1910/);
  assert.equal(h.button('Apply').props.disabled,false);h.destroy();
});

test('A backend restart starts a new revision sequence without discarding the new read',async()=>{
  let count=0;
  const h=harness(defaultState,true,route=>route==='read_calibration'?{
    ...defaultState,current:{...current,lmax:count?1910:1900},
    state_epoch:count?'new-backend':'old-backend',state_revision:count++?1:100}:undefined);
  await h.mount();h.button('Read Calibration').props.onClick();await h.tick();h.render();
  assert.match(h.text(),/Current MAX: 1910/);
  assert.equal(h.button('Apply').props.disabled,false);h.destroy();
});

test('Display freshness cannot veto explicit backend Apply permission',async()=>{
  let reads=0;
  const h=harness(defaultState,true,route=>route==='read_calibration'?{
    ...defaultState,read_status:reads?'Error':'Success',current_is_stale:reads++>0,
    manual_apply_enabled:true}:undefined);await h.mount();
  h.button('Read Calibration').props.onClick();await h.tick();h.render();
  assert.equal(h.button('Apply').props.disabled,false);
  h.button('Apply').props.onClick();h.modals[0].props.onOK();await h.tick();
  assert.equal(h.calls.filter(c=>c.route==='apply_selected').length,1);h.destroy();
});

test('Sliders are interactive even when hardware Apply is unavailable',async()=>{
  const h=harness({...defaultState,manual_apply_enabled:false});await h.mount();
  const sliders=h.nodes.filter(n=>n.type==='SliderField');
  assert.equal(sliders.length,2); // every([]) previously let missing controls pass.
  assert.ok(sliders.every(n=>n.props.disabled===false));
  assert.equal(h.button('Apply').props.disabled,true);h.destroy();
});

test('Calibration Settings stays visible across backend states, without windows or backup',async()=>{
  const cases=[
    {state:{...defaultState,windows:undefined},sliders:2},
    {state:{...defaultState,windows:null,ok:false,error:'Conflict prevents Apply',manual_apply_enabled:false},sliders:2},
    {state:{...defaultState,initial:null,windows:null},sliders:2},
    {state:{...defaultState,windows:{l:{min:NaN,max:NaN},r:{min:10,max:10}}},sliders:2},
    {state:{ok:false,error:'Hardware read failed'},sliders:0},
    {state:{...defaultState,current:null,error:'Controller unavailable'},sliders:0},
    {state:{...defaultState,current:{...current,lmax:NaN}},sliders:0},
  ];
  for(const item of cases) {
    const h=harness(item.state);await h.mount();
    assert.equal(h.nodes.filter(n=>n.type==='PanelSection'&&n.props.title===(item.sliders?'Calibration Settings':'Trigger Calibration')).length,1);
    const sliders=h.nodes.filter(n=>n.type==='SliderField');
    assert.equal(sliders.length,item.sliders);
    assert.ok(sliders.every(n=>Number.isFinite(n.props.min)&&Number.isFinite(n.props.max)&&
      n.props.min<n.props.max&&n.props.value>=n.props.min&&n.props.value<=n.props.max));
    if(item.sliders<2)assert.ok(h.nodes.some(n=>n.props.role==='alert'));
    if(!item.state.initial&&item.sliders)assert.match(h.text(),/Default: Unavailable/);
    assert.equal(!!h.button('Open Trigger Test'),item.sliders>0);h.destroy();
  }
});

test('Both native sliders are direct Decky row children, with separate marker rows',async()=>{
  const h=harness();await h.mount();
  const rows=h.nodes.filter(n=>n.type==='PanelSectionRow'&&n.props.children?.type==='SliderField');
  assert.equal(rows.length,2);
  assert.equal(h.nodes.filter(n=>n.type==='PanelSection')[0].props.title,'Calibration Settings');
  assert.match(h.text(),/Selected MAX: 1900/);assert.match(h.text(),/Current MAX: 1850/);h.destroy();
});

test('Fast independent slider changes only persist preferences and never Apply',async()=>{
  const h=harness();await h.mount();
  const sliders=h.nodes.filter(n=>n.type==='SliderField');
  sliders[0].props.onChange(1890);sliders[1].props.onChange(1870);
  await h.tick();h.render();
  const selections=h.calls.filter(c=>c.route==='select_values');
  assert.deepEqual(Array.from(selections.at(-1).args),[1890,1870]);
  assert.equal(h.calls.some(c=>c.route==='apply_selected'),false);
  assert.match(h.text(),/Current MAX: 1900/);assert.match(h.text(),/Current MAX: 1850/);h.destroy();
});

test('Exactly two thin non-intercepting reference markers per slider',async()=>{
  const h=harness();await h.mount();
  const markers=h.nodes.filter(n=>n.props['data-reference']);
  assert.deepEqual(markers.map(n=>n.props['data-reference']),['Default','Caution','Default','Caution']);
  assert.ok(markers.every(n=>n.props.style.width===2));
  assert.deepEqual(markers.map(n=>n.props.style.left),['100%','20%','50%','20%']);
  assert.ok(h.nodes.filter(n=>n.props['aria-label']==='Calibration reference markers')
    .every(n=>n.props.style.pointerEvents==='none'));h.destroy();
});

test('Loading and unsupported native SliderField keep Calibration Settings visible',async()=>{
  const loading=harness();loading.render();
  assert.equal(loading.nodes.filter(n=>n.type==='PanelSection'&&n.props.title==='Trigger Calibration').length,1);
  assert.match(loading.text(),/Read the current calibration to begin\./);
  assert.doesNotMatch(loading.text(),/Unavailable|Selected MAX|Current MAX|Default:/);loading.destroy();
  const unavailable=harness(defaultState,false);await unavailable.mount();
  assert.match(unavailable.text(),/Decky SliderField is unavailable/);
  assert.equal(unavailable.nodes.filter(n=>n.type==='SliderField').length,0);
  assert.ok(unavailable.button('Open Trigger Test'));unavailable.destroy();
});

test('Apply confirms the actual selected values before writing',async()=>{
  const h=harness();await h.mount();h.button('Apply').props.onClick();
  assert.equal(h.calls.some(c=>c.route==='apply_selected'),false);
  assert.match(h.modals[0].props.strDescription,/No automatic rollback/);
  h.modals[0].props.onOK();await h.tick();
  assert.equal(h.calls.filter(c=>c.route==='apply_selected').length,1);h.destroy();
});

test('Restore remains accessible on read failure and never guesses defaults',async()=>{
  const h=harness({...defaultState,current:null,ok:false,error:'Read unavailable'});await h.mount();
  assert.equal(h.button('Restore Original').props.disabled,false);
  h.button('Restore Original').props.onClick();
  assert.match(h.modals[0].props.strDescription,/not factory recalibration/);
  assert.equal(h.calls.some(c=>c.route==='restore_default'),false);h.destroy();
});

test('No automatic profile controls are rendered or called',async()=>{
  const h=harness();await h.mount();
  assert.equal(h.nodes.some(n=>n.type==='ToggleField'&&n.props.label.startsWith('Apply')),false);
  assert.equal(h.calls.some(c=>c.route==='set_automatic'),false);h.destroy();
});

test('First installation offers explicit Original capture without changing the normal controls',async()=>{
  const normal=harness();await normal.mount();
  assert.equal(normal.button('Save Original Calibration'),undefined);normal.destroy();
  const h=harness({...defaultState,ok:false,error:'Original Calibration is not saved.',initial:null,
    manual_apply_enabled:false,can_set_current_as_default:true});await h.mount();
  assert.equal(h.nodes.filter(n=>n.type==='SliderField').length,2);
  h.button('Save Original Calibration').props.onClick();
  assert.equal(h.calls.some(c=>c.route==='set_current_as_default'),false);
  assert.match(h.modals[0].props.strDescription,/not guaranteed factory calibration/);
  h.modals[0].props.onOK();await h.tick();
  const call=h.calls.find(c=>c.route==='set_current_as_default');
  assert.deepEqual(Array.from(call.args),[true]);h.destroy();
});

test('Trigger Test displays report-scale bars and stops its reader on close',async()=>{
  const h=harness();await h.mount();assert.equal(h.calls.some(c=>c.route==='start_input'),false);
  assert.equal(h.plugin.alwaysRender,false);h.button('Open Trigger Test').props.onClick();
  h.render();h.runEffects();await h.tick();h.render();
  const bars=h.nodes.filter(n=>n.props.role==='progressbar');
  assert.equal(bars.length,2);assert.equal(bars[0].props['aria-valuenow'],0);assert.equal(bars[1].props['aria-valuenow'],100);
  assert.doesNotMatch(h.text(),/HID|Samples:|reports received|Full Analog Travel/);
  assert.equal(h.calls.filter(c=>c.route==='start_input').length,1);
  h.destroy();await h.tick();assert.equal(h.calls.filter(c=>c.route==='stop_input').length,1);
});

test('Unread session stays minimal with emergency Restore, and read sliders reach 1880',async()=>{
  const archived={...defaultState,current:null,error:'Read failed'};
  const h=harness(archived);await h.mount();
  assert.match(h.text(),/Read the current calibration to begin/);
  assert.doesNotMatch(h.text(),/Unavailable|LT MAX:|RT MAX:/);
  assert.ok(h.button('Restore Original'));h.destroy();
  const values={...current,lmax:1661,rmax:1691};
  const normal=harness({...defaultState,current:values,initial:{values},selected:{lmax:1820,rmax:1820}});
  await normal.mount();
  const sliders=normal.nodes.filter(n=>n.type==='SliderField');
  assert.equal(sliders.length,2);
  assert.equal(sliders[0].props.min,sliders[1].props.min);
  assert.equal(sliders[0].props.max,1880);assert.equal(sliders[1].props.max,1880);
  sliders[0].props.onChange(1880);await normal.tick();normal.render();
  assert.equal(normal.calls.filter(c=>c.route==='select_values').at(-1).args[0],1880);
  assert.equal(normal.calls.some(c=>c.route==='apply_selected'),false);normal.destroy();
});

test('Fresh panel does not read hardware or render settings until the user chooses Read',async()=>{
  const h=harness();await h.mount(false);
  assert.equal(h.calls.filter(c=>c.route==='read_calibration').length,0);
  assert.equal(h.nodes.filter(n=>n.type==='SliderField').length,0);
  assert.doesNotMatch(h.text(),/Unavailable|Selected MAX|Current MAX|Default:|Original Calibration/);
  h.button('Read Calibration').props.onClick();await h.tick();h.render();
  assert.equal(h.nodes.filter(n=>n.type==='SliderField').length,2);
  assert.match(h.text(),/🔒 Original Calibration/);
  assert.equal(h.button('Save Original Calibration'),undefined);
  assert.ok(h.button('Export Original Backup'));h.destroy();
});

test('Explicit capture hides Save permanently while Read preserves archived and selected values',async()=>{
  let saved=false;
  const first={...defaultState,initial:null,can_set_current_as_default:true};
  const h=harness(first,true,route=>{
    if(route==='set_current_as_default')saved=true;
    if(saved && ['set_current_as_default','read_calibration'].includes(route))return {...defaultState,
      can_set_current_as_default:false,current:{...current,lmax:1820,rmax:1820},selected:{lmax:1880,rmax:1880}};
  });
  await h.mount();h.button('Save Original Calibration').props.onClick();
  assert.match(h.modals[0].props.strDescription,/fully shut down/);
  h.modals[0].props.onOK();await h.tick();h.render();
  assert.equal(h.button('Save Original Calibration'),undefined);
  h.button('Read Calibration').props.onClick();await h.tick();h.render();
  assert.equal(h.button('Save Original Calibration'),undefined);
  assert.match(h.text(),/LT MAX: 1900/);assert.match(h.text(),/Selected MAX: 1880/);
  assert.match(h.text(),/Current MAX: 1820/);h.destroy();
});

test('Confirmation remount preserves Read -> Apply -> Adjust -> Apply -> Trigger Test -> Restore',async()=>{
  const original={lmax:1703,lmin:611,rmax:1727,rmin:623,lneg:12,rneg:13};
  let backend={...defaultState,current:original,initial:{values:original},selected:{lmax:1703,rmax:1727}};
  let applies=0,finishSecond;
  const h=harness(backend,true,async(route,args)=>{
    if(route==='select_values')return backend={...backend,selected:{lmax:args[0],rmax:args[1]}};
    if(route==='apply_selected'){
      assert.deepEqual(JSON.parse(JSON.stringify(args[0])),backend.current);
      assert.equal(args[3],true);
      if(++applies===2)await new Promise(resolve=>finishSecond=resolve);
      const actual={...backend.current,lmax:args[1],rmax:args[2]};
      return backend={...backend,current:actual,message:'Calibration Applied',current_is_stale:false,
        last_apply:{status:'verified',readback:actual},manual_apply_enabled:true};
    }
    if(route==='restore_default')return backend={...backend,current:original,message:'Default restored and read back.',
      current_is_stale:false,last_apply:{status:'verified',readback:original},manual_apply_enabled:true};
    if(['get_status','read_calibration'].includes(route))return backend;
  });
  await h.mount();
  h.nodes.find(n=>n.type==='ToggleField').props.onChange(true);h.render();
  h.nodes.find(n=>n.type==='SliderField').props.onChange(1743);await h.tick();h.render();
  h.button('Apply').props.onClick();
  await h.remount(); // QAM closes for the modal before the user confirms.
  assert.equal(h.nodes.filter(n=>n.type==='SliderField').length,2);
  h.modals.at(-1).props.onOK();await h.tick();h.render();
  assert.match(h.text(),/Current MAX: 1743/);assert.match(h.text(),/Calibration Applied/);
  assert.equal(h.nodes.find(n=>n.type==='ToggleField').props.checked,true);
  assert.equal(h.button('Apply').props.disabled,false);
  h.nodes.find(n=>n.type==='SliderField').props.onChange(1783);await h.tick();h.render();
  h.button('Apply').props.onClick();h.modals.at(-1).props.onOK();await h.tick();
  await h.remount(); // Also cover remount while the existing write RPC is pending.
  assert.equal(h.nodes.filter(n=>n.type==='SliderField').length,2);
  assert.equal(h.button('Apply').props.disabled,true);
  finishSecond();await h.tick();h.render();
  assert.match(h.text(),/Current MAX: 1783/);assert.match(h.text(),/Selected MAX: 1783/);
  assert.equal(h.button('Apply').props.disabled,false);
  assert.equal(h.calls.filter(c=>c.route==='start_input').length,0);
  h.button('Open Trigger Test').props.onClick();h.render();h.runEffects();await h.tick();h.render();
  assert.equal(h.nodes.filter(n=>n.props.role==='progressbar').length,2);
  h.button('Close Trigger Test').props.onClick();h.render();h.runEffects();await h.tick();
  h.button('Restore Original').props.onClick();await h.remount();
  h.modals.at(-1).props.onOK();await h.tick();h.render();
  assert.equal(h.nodes.filter(n=>n.type==='SliderField').length,2);
  assert.match(h.text(),/Current MAX: 1703/);assert.match(h.text(),/Selected MAX: 1783/);
  assert.match(h.text(),/Default: 1703/);assert.equal(h.button('Apply').props.disabled,false);
  assert.equal(h.calls.filter(c=>c.route==='read_calibration').length,1);
  assert.equal(h.calls.filter(c=>c.route==='get_status').length,1);
  assert.equal(h.calls.filter(c=>c.route==='apply_selected').length,2);
  assert.equal(h.calls.filter(c=>c.route==='stop_input').length,1);
  await h.remount(); // An ordinary later opening keeps the existing initial screen.
  assert.equal(h.nodes.filter(n=>n.type==='SliderField').length,0);h.destroy();
});

test('Confirmation remount retains Last Read and selections on unverified or disconnected Apply',async()=>{
  for(const disconnect of [false,true]){
    const h=harness(defaultState,true,route=>{
      if(route==='apply_selected'){
        if(disconnect)throw new Error('Connection lost after write');
        return {ok:false,error:'Calibration verification pending',current:null,initial:null,selected:null,manual_apply_enabled:false};
      }
    });
    await h.mount();h.nodes.find(n=>n.type==='SliderField').props.onChange(1890);await h.tick();h.render();
    h.button('Apply').props.onClick();await h.remount();h.modals.at(-1).props.onOK();await h.tick();h.render();
    assert.equal(h.nodes.filter(n=>n.type==='SliderField').length,2);
    assert.match(h.text(),/Selected MAX: 1890/);assert.match(h.text(),/Last Read: 1900/);
    assert.match(h.text(),/Default: 1900/);assert.equal(h.button('Apply').props.disabled,true);
    assert.doesNotMatch(h.text(),/Calibration Applied|Read the current calibration to begin/);
    h.button('Read Calibration').props.onClick();await h.tick();h.render();
    assert.equal(h.button('Apply').props.disabled,false);h.destroy();
  }
});

test('Confirmation dismissal restores the active screen without writing hardware',async()=>{
  const h=harness();await h.mount();h.button('Apply').props.onClick();await h.remount();
  h.closeModal();await h.tick();h.render();
  assert.equal(h.nodes.filter(n=>n.type==='SliderField').length,2);
  assert.equal(h.button('Apply').props.disabled,false);
  assert.equal(h.calls.filter(c=>c.route==='apply_selected').length,0);
  await h.remount();h.button('Restore Original').props.onClick();await h.remount();
  h.closeModal();await h.tick();h.render();
  assert.equal(h.nodes.filter(n=>n.type==='SliderField').length,0);
  assert.equal(h.calls.filter(c=>c.route==='restore_default').length,0);h.destroy();
});
