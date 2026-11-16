import React from 'react';
import * as JSX from 'react/jsx-runtime';
import {createRoot} from 'react-dom/client';
const e=React.createElement;
window.SP_REACT=React;window.SP_JSX=JSX;
const current={lmax:1611,lmin:518,rmax:1647,rmin:506,lneg:12,rneg:13};
let state={ok:true,current,initial:{values:current,provenance:'Synthetic browser fixture'},
  selected:{lmax:1611,rmax:1647},profiles:{lkg:null},manual_apply_enabled:false};
if(location.search.includes('error'))state={ok:false,error:'Synthetic calibration read failure'};
if(location.search.includes('no-backup'))state.initial=null;
if(location.search.includes('writable'))state.manual_apply_enabled=true;
window.rpcCalls=[];
window.__DECKY_SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED_deckyLoaderAPIInit={connect:()=>({
  _version:2,callable:route=>async(...args)=>{
    window.rpcCalls.push({route,args});
    if(route==='select_values')state={...state,selected:{lmax:args[0],rmax:args[1]}};
    if(route==='start_input'||route==='input_state')return {ok:true,token:'fixture',active:true,values:[0,16384],percent:[0,50],samples:25};
    if(route==='stop_input')return{ok:true};
    return state;
  }
})};
// DOM STAND-INS only: the real Decky/Steam controls are unavailable here.
window.DFL={
  staticClasses:{Title:'title'},
  PanelSection:({title,children})=>e('section',{'data-section':title},title&&e('h2',null,title),children),
  PanelSectionRow:({children})=>e('div',{className:'row'},children),
  ButtonItem:({children,disabled,onClick})=>e('button',{disabled,onClick},children),
  ToggleField:({label,checked,disabled,onChange})=>e('label',null,label,e('input',{type:'checkbox',checked,disabled,onChange:event=>onChange(event.target.checked)})),
  SliderField:({label,value,min,max,step,disabled,onChange})=>e('label',{className:'slider'},label,
    e('input',{'aria-label':label,type:'range',value,min,max,step,disabled,onChange:event=>onChange(Number(event.target.value))})),
  ConfirmModal:()=>null,showModal:()=>{}
};
const plugin=await import(location.search.includes('baseline')?'/plugin-old.js':'/plugin.js');
createRoot(document.getElementById('app')).render(plugin.default().content);
