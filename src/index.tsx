import { staticClasses } from '@decky/ui';
import { definePlugin } from '@decky/api';
import { Content } from './Content';

export default definePlugin(() => ({
  name: 'Trigger Calibration',
  alwaysRender: false,
  titleView: <div className={staticClasses.Title}>Trigger Calibration</div>,
  content: <Content />,
  icon: <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2"
    aria-label="Trigger Calibration"><path d="M3 6h18M3 12h18M3 18h18" />
    <path d="M8 3v6M16 9v6M10 15v6" strokeWidth="4" /></svg>,
}));
