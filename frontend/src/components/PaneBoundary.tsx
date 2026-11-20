import { Component, type ErrorInfo, type JSX, type ReactNode } from "react";

interface Props {
  readonly name: string;
  readonly children: ReactNode;
}

interface State {
  readonly failed: boolean;
}

/**
 * One pane failing never blanks the app (docs/18 §10). The pane is replaced by a short notice;
 * the rest keeps working. A class because React offers error boundaries no other way.
 */
export class PaneBoundary extends Component<Props, State> {
  override state: State = { failed: false };

  static getDerivedStateFromError(): State {
    return { failed: true };
  }

  override componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error(`${this.props.name} pane failed`, error, info.componentStack);
  }

  override render(): JSX.Element {
    if (this.state.failed) {
      return (
        <section className="panel panel--warn" role="alert">
          <h2 className="panel__title">{this.props.name}</h2>
          <p className="panel__body">This part of the app hit an error and stopped.</p>
          <button
            className="button button--small"
            type="button"
            onClick={() => {
              this.setState({ failed: false });
            }}
          >
            Try again
          </button>
        </section>
      );
    }
    return <>{this.props.children}</>;
  }
}
