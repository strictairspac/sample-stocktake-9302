import {COUNT_STATES,LINE_RESULTS} from '../../constants/enums.js';
import {esc} from '../../utils/html.js';
export const countBadge=value=>`<span class="badge count-${esc(value)}">${esc(COUNT_STATES[value]||value)}</span>`;
export const lineBadge=value=>`<span class="badge line-${esc(value)}">${esc(LINE_RESULTS[value]||value)}</span>`;
