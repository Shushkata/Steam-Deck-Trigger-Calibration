export type Values = { lmax: number; lmin: number; rmax: number; rmin: number; lneg: number; rneg: number };
export const CAUTION = 1820;
export const SLIDER_MAX = 1880;

export function markerPosition(value: number, min: number, max: number): number | null {
  return max > min && value >= min && value <= max ? (value - min) / (max - min) * 100 : null;
}

export function cautionMessage(value: number, original: number, caution = CAUTION): string {
  return value > caution && value > original ? 'Caution: Test trigger response carefully.' : '';
}

export function validProposal(current: Values | null | undefined, left: number, right: number): boolean {
  return !!current && Number.isInteger(left) && Number.isInteger(right) && left <= 65535 && right <= 65535 &&
    left > current.lmin && right > current.rmin;
}

export function difference(value: number, original: number): string {
  const delta = value-original;
  return delta > 0 ? `+${delta}` : `${delta}`;
}

export type TriggerControls = {
  current: number | null; minimum: number | null; original: number | null;
  selected: number | null; range: { min: number; max: number } | null;
  error: string | null;
};

const wireInteger = (value: unknown): value is number =>
  typeof value === 'number' && Number.isInteger(value) && value >= 0 && value <= 65535;

/** Rendering uses validated numeric readings, not Apply permission, overall ok,
 * or optional backend viewport metadata. No fallback is a factory default. */
export function triggerControls(current: Partial<Values> | null | undefined,
  initial: Partial<Values> | null | undefined, selected: number, side: 'l' | 'r', otherSelected?:number): TriggerControls {
  const maximum = current?.[`${side}max`];
  const minimum = current?.[`${side}min`];
  const original = initial?.[`${side}max`];
  const originalValue = wireInteger(original) ? original : null;
  if (!wireInteger(maximum) || !wireInteger(minimum) || maximum <= minimum) {
    return {current:null,minimum:null,original:originalValue,selected:null,range:null,
      error:'Calibration controls unavailable: a valid current MAX/MIN reading is required. Press Read Calibration.'};
  }
  const knownSelected = wireInteger(selected);
  const validSelected = knownSelected && selected > minimum;
  const value = knownSelected ? selected : maximum;
  const anchors = [current?.lmax,current?.rmax,initial?.lmax,initial?.rmax,CAUTION].filter(wireInteger);
  const minima=[current?.lmin,current?.rmin].filter(wireInteger);
  const selections=[value];
  const peerMin=current?.[side==='l'?'rmin':'lmin'];
  if(wireInteger(otherSelected) && wireInteger(peerMin)) selections.push(otherSelected);
  // Keep the viewport/Default marker stationary while dragging. Extend only
  // to include an already persisted selection outside the normal viewport.
  const min = Math.min(...selections,Math.max(Math.min(...minima)+1,Math.min(...anchors)-20));
  const max = Math.max(SLIDER_MAX,...selections,...anchors);
  if (max <= min || minimum===65534) {
    return {current:maximum,minimum,original:originalValue,selected:value,range:null,
      error:'Calibration controls unavailable: no adjustable interval exists for this reading.'};
  }
  return {current:maximum,minimum,original:originalValue,selected:value,range:{min,max},
    error:validSelected ? null : knownSelected ? 'Selected MAX is not above the current MIN. Adjust it before Apply; the saved selection was kept.' : 'The selected MAX is invalid for this reading. The control shows Current MAX; no value was saved or applied.'};
}
