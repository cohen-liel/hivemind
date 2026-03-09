import React from 'react';
import CodeBrowser from './CodeBrowser';

// ============================================================================
// Props Interface
// ============================================================================

export interface CodePanelProps {
  projectId: string;
}

// ============================================================================
// CodePanel — Wrapper for CodeBrowser in the project view tabs
// ============================================================================

/** Code browser tab content for both mobile and desktop layouts. */
const CodePanel = React.memo(function CodePanel({ projectId }: CodePanelProps): React.ReactElement {
  return <CodeBrowser projectId={projectId} />;
});

export default CodePanel;
