import type {
  ConsoleMessage,
  Page,
  Request,
  Response,
  Worker,
} from '@playwright/test';

export const reactWorkspaceCsp = [
  "default-src 'self'",
  "script-src 'self'",
  "worker-src 'self'",
  "connect-src 'self'",
  "style-src 'self' 'unsafe-inline'",
  "font-src 'self' data:",
  "object-src 'none'",
  "base-uri 'self'",
  "frame-ancestors 'none'",
].join('; ');

export interface RuntimeAuditOptions {
  readonly enforceCsp?: boolean;
}

export interface RuntimeAuditAssertionOptions {
  readonly requireFont?: boolean;
  readonly requireWorker?: boolean;
  readonly allowFailedRequest?: (request: Request) => boolean;
}

export class RuntimeAudit {
  readonly consoleFailures: string[] = [];
  readonly pageFailures: string[] = [];
  readonly failedRequests: Request[] = [];
  readonly responses: Response[] = [];
  readonly requests: Request[] = [];
  readonly workers = new Set<Worker>();
  readonly openedWorkers: Worker[] = [];

  private readonly page: Page;

  constructor(page: Page) {
    this.page = page;
  }

  recordConsole(message: ConsoleMessage): void {
    if (message.type() === 'error' || message.type() === 'warning') {
      this.consoleFailures.push(`${message.type()}: ${message.text()}`);
    }
  }

  recordWorker(worker: Worker): void {
    this.workers.add(worker);
    this.openedWorkers.push(worker);
    worker.on('close', () => this.workers.delete(worker));
  }

  async cspViolations(): Promise<string[]> {
    return this.page.evaluate(() => {
      const value = Reflect.get(globalThis, '__paperStudyCspViolations');
      return Array.isArray(value) ? value.map(String) : [];
    });
  }

  legacyRequests(): Request[] {
    return this.requests.filter((request) => {
      const url = new URL(request.url());
      const pathname = url.pathname.toLocaleLowerCase();
      return pathname.startsWith('/legacy/')
        || pathname.startsWith('/public/')
        || pathname === '/app.js'
        || pathname === '/style.css'
        || pathname === '/index.html'
        || pathname.endsWith('/spatial-workspace.js')
        || pathname.includes('/vendor/pdfjs/');
    });
  }

  mimeFailures(): string[] {
    const failures: string[] = [];
    for (const response of this.responses) {
      const request = response.request();
      const type = request.resourceType();
      const contentType = response.headers()['content-type']?.toLocaleLowerCase() ?? '';
      if (type === 'script' && !contentType.includes('javascript')) {
        failures.push(`script MIME ${contentType || '<missing>'}: ${response.url()}`);
      }
      if (type === 'stylesheet' && !contentType.includes('text/css')) {
        failures.push(`stylesheet MIME ${contentType || '<missing>'}: ${response.url()}`);
      }
      if (
        type === 'font'
        && !contentType.startsWith('font/')
        && !contentType.includes('application/font')
        && !contentType.includes('application/octet-stream')
      ) {
        failures.push(`font MIME ${contentType || '<missing>'}: ${response.url()}`);
      }
      if (response.url().includes('worker') && type === 'script' && !contentType.includes('javascript')) {
        failures.push(`worker MIME ${contentType || '<missing>'}: ${response.url()}`);
      }
    }
    return failures;
  }

  async assertClean(options: RuntimeAuditAssertionOptions = {}): Promise<void> {
    const requestFailures = this.failedRequests.filter(
      (request) => {
        if (options.allowFailedRequest?.(request)) return false;
        const errorText = request.failure()?.errorText.toLocaleLowerCase() ?? '';
        return !errorText.includes('aborted')
          && !errorText.includes('cancelled')
          && !errorText.includes('canceled');
      },
    );
    const responseFailures = this.responses
      .filter((response) => response.status() >= 400)
      .map((response) => `${response.status()} ${response.url()}`);
    const violations = await this.cspViolations();
    const legacy = this.legacyRequests().map((request) => request.url());
    const mime = this.mimeFailures();
    const missing: string[] = [];
    if (options.requireWorker && this.openedWorkers.length === 0) {
      missing.push('no Worker was created');
    }
    if (
      options.requireFont
      && !this.responses.some((response) => response.request().resourceType() === 'font')
    ) {
      missing.push('no font response was observed');
    }
    const failureReport = [
      ...this.consoleFailures,
      ...this.pageFailures,
      ...requestFailures.map((request) => `request failed: ${request.url()}`),
      ...responseFailures.map((failure) => `response failed: ${failure}`),
      ...violations.map((violation) => `CSP violation: ${violation}`),
      ...legacy.map((url) => `legacy request: ${url}`),
      ...mime,
      ...missing,
    ];
    if (failureReport.length > 0) {
      throw new Error(`Runtime audit failed:\n${failureReport.join('\n')}`);
    }
  }
}

export async function installRuntimeAudit(
  page: Page,
  options: RuntimeAuditOptions = {},
): Promise<RuntimeAudit> {
  const audit = new RuntimeAudit(page);
  await page.addInitScript(() => {
    const violations: string[] = [];
    Reflect.set(globalThis, '__paperStudyCspViolations', violations);
    document.addEventListener('securitypolicyviolation', (event) => {
      violations.push(`${event.violatedDirective}: ${event.blockedURI}`);
    });
  });

  if (options.enforceCsp) {
    await page.route('**/workspace/**', async (route) => {
      if (route.request().resourceType() !== 'document') {
        await route.fallback();
        return;
      }
      const response = await route.fetch();
      await route.fulfill({
        response,
        headers: {
          ...response.headers(),
          'content-security-policy': reactWorkspaceCsp,
        },
      });
    });
  }

  page.on('console', (message) => audit.recordConsole(message));
  page.on('pageerror', (error) => audit.pageFailures.push(`page error: ${error.message}`));
  page.on('request', (request) => audit.requests.push(request));
  page.on('requestfailed', (request) => audit.failedRequests.push(request));
  page.on('response', (response) => audit.responses.push(response));
  page.on('worker', (worker) => audit.recordWorker(worker));
  return audit;
}
