/**
 * Render-error containment.
 *
 * WHY THIS EXISTS. React unmounts the entire tree when a render throws, so before
 * this component a single malformed payload — one unexpected `undefined` in an
 * audit record, one shape change in an API response — replaced the whole command
 * centre with a blank page. In a live demo that is unrecoverable, and it hides the
 * one thing an operator needs: what actually broke.
 *
 * React error boundaries only catch errors thrown during *rendering*, in a
 * lifecycle method, or in a constructor. They do not catch errors in event
 * handlers, in `setTimeout`, or in a rejected promise — those are already handled
 * by the API client, which converts a failed fetch into a typed `GovernanceRefusal`
 * with a legible message. `componentDidCatch` here is therefore the last line for
 * the class of failure that would otherwise be silent: a bad render.
 *
 * The fallback deliberately shows the error text. Hiding it behind "Something went
 * wrong" would be friendlier and less useful; this is an operator tool, and the
 * message is the diagnostic.
 */

import { Component, type ErrorInfo, type ReactNode } from "react";

interface ErrorBoundaryProps {
  children: ReactNode;
  /** Shown above the detail, so a failure names the surface it broke. */
  label?: string;
  /** Called on every catch. Defaults to console.error; injected in tests. */
  onError?: (error: Error, info: ErrorInfo) => void;
}

interface ErrorBoundaryState {
  error: Error | null;
  componentStack: string | null;
}

export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null, componentStack: null };

  static getDerivedStateFromError(error: Error): Partial<ErrorBoundaryState> {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    this.setState({ componentStack: info.componentStack ?? null });
    const report = this.props.onError;
    if (report) {
      report(error, info);
      return;
    }
    // Kept as console.error rather than a network report: this build is
    // offline-first, and a crash reporter that needs the network would be
    // unavailable exactly when the app is already broken.
    console.error("SANJEEVANI render error", error, info.componentStack);
  }

  private reset = (): void => {
    this.setState({ error: null, componentStack: null });
  };

  render(): ReactNode {
    const { error, componentStack } = this.state;
    if (!error) {
      return this.props.children;
    }
    const label = this.props.label ?? "The command center";
    return (
      <div className="crash" role="alert" aria-live="assertive">
        <h1 className="crash__title">{label} stopped rendering.</h1>
        <p className="crash__lede">
          This is a display failure, not a change of state: no plan was approved and
          nothing was executed. The backend holds the authoritative record, so the
          audit trail is intact — reload to render it again.
        </p>
        <pre className="crash__detail" tabIndex={0} role="region" aria-label="Error detail">
          {error.message}
          {componentStack ? `\n${componentStack.trim()}` : ""}
        </pre>
        <div className="crash__actions">
          <button type="button" className="btn btn--primary" onClick={() => window.location.reload()}>
            Reload the command center
          </button>
          <button type="button" className="btn" onClick={this.reset}>
            Try rendering again
          </button>
          <a className="crash__link" href="/api/state">
            Read the raw state from the API
          </a>
        </div>
      </div>
    );
  }
}
