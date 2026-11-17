import {countsApi} from '../api/counts.js';
export const countsStore={items:[],async refresh(){this.items=await countsApi.list();return this.items;}};
