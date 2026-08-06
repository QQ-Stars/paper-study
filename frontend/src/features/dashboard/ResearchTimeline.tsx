import type {
  ResearchTimelineEvidence,
  TimelineSource,
} from './evidence';
import { deriveTimelineEvents } from './evidence';

export type {
  DashboardJobEvidence,
  DashboardPaperEvidence,
  DashboardReviewEvidence,
} from './evidence';

function sourceLabel(source: TimelineSource): string {
  switch (source) {
    case 'paper': return '论文';
    case 'review': return '复习';
    case 'job': return '任务';
  }
}

function formatTimestamp(timestamp: string): string {
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(timestamp));
}

export function ResearchTimeline(props: ResearchTimelineEvidence) {
  const events = deriveTimelineEvents(props);

  return (
    <section className="research-timeline" aria-labelledby="research-timeline-heading">
      <header className="research-timeline__header">
        <p>VERIFIED ACTIVITY</p>
        <h2 id="research-timeline-heading">研究时间线</h2>
      </header>

      {events.length === 0 ? (
        <div className="research-timeline__empty" role="status">
          <strong>没有可核验的时间线事件</strong>
          <span>仅显示论文、复习和任务返回的真实时间字段，不生成示例活动。</span>
        </div>
      ) : (
        <ol className="research-timeline__list" aria-label="真实研究时间线">
          {events.map((event) => (
            <li key={event.id} className="research-timeline__event">
              <div className="research-timeline__source">{sourceLabel(event.source)}</div>
              <div>
                <strong>{event.title}</strong>
                <p>{event.detail}</p>
                <time dateTime={event.timestamp}>{formatTimestamp(event.timestamp)}</time>
              </div>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
