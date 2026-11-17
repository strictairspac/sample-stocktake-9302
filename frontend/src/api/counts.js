import {api} from './client.js';
export const countsApi={
  list:()=>api('/counts'),
  detail:id=>api('/counts/'+id),
  freeze:data=>api('/counts','POST',data),
  enter:(id,lines)=>api(`/counts/${id}/lines`,'PUT',{lines}),
  post:id=>api(`/counts/${id}/post`,'POST',{})
};
