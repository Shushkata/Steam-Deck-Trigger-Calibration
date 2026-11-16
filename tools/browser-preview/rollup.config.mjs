import {createRequire} from 'node:module';
import {realpathSync} from 'node:fs';
const require=createRequire(realpathSync(new URL('../../node_modules/@decky/rollup/src/index.js',import.meta.url)));
const {nodeResolve}=require('@rollup/plugin-node-resolve');
const commonjs=require('@rollup/plugin-commonjs');
export default {input:'entry.mjs',plugins:[nodeResolve({browser:true}),commonjs()],output:{file:'preview.js',format:'esm'}};
