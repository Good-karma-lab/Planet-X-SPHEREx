// An exception while a screen renders: without a boundary Preact drops the whole tree under it and leaves an empty black page. The
// screen says so and offers a reload; the rail and the other screens go on. The error's text goes to the console.
import { Component, type ComponentChildren } from 'preact';
import { isChunkError, reloadOnce } from '../lib/newbuild';

export class ErrorBoundary extends Component<{ name: string; at?: string; children: ComponentChildren }, { failed: boolean }> {
  override state = { failed: false };

  override componentDidCatch(e: unknown): void {
    console.error(`the ${this.props.name} screen failed`, e);
    if (isChunkError(e) && reloadOnce()) return;
    this.setState({ failed: true });
  }

  /** another place of the same screen (`at`: its address) is another try */
  override componentDidUpdate(before: { at?: string }): void {
    if (this.state.failed && before.at !== this.props.at) this.setState({ failed: false });
  }

  override render() {
    if (!this.state.failed) return this.props.children;
    return (
      <div class="screen-failed" role="alert" data-testid="screen-failed">
        <b>Something went wrong here</b>
        <button class="btn primary" onClick={() => location.reload()} data-testid="screen-reload">
          Reload
        </button>
      </div>
    );
  }
}
