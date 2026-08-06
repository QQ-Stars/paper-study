import type { JobDetail } from '../../lib/api/types';

export function jobDetailPollingIntervalFor(
  detail: JobDetail | undefined,
  candidatesExpanded: boolean,
): 2500 | false {
  const status = detail?.job.status;
  return !candidatesExpanded && (status === 'pending' || status === 'running')
    ? 2500
    : false;
}
