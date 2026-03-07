interface Props {
  description: string;
  projectId: string;
  onClose: () => void;
}

export default function ApprovalModal({ description, projectId, onClose }: Props) {
  const handleApprove = async () => {
    await fetch(`/api/projects/${projectId}/approve`, { method: 'POST' });
    onClose();
  };

  const handleReject = async () => {
    await fetch(`/api/projects/${projectId}/reject`, { method: 'POST' });
    onClose();
  };

  return (
    <div className="fixed inset-0 bg-black/60 backdrop-blur-sm z-50 flex items-center justify-center p-4"
      onClick={onClose}>
      <div
        className="bg-gray-900 border border-gray-800 rounded-2xl p-6 max-w-md w-full shadow-2xl animate-[fadeSlideIn_0.2s_ease-out]"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center gap-3 mb-4">
          <div className="w-10 h-10 rounded-full bg-yellow-500/20 flex items-center justify-center text-xl">
            {'\u{1F6D1}'}
          </div>
          <div>
            <h3 className="text-sm font-bold text-white">Approval Required</h3>
            <p className="text-[11px] text-gray-500">The orchestrator needs your permission to proceed</p>
          </div>
        </div>

        {/* Description */}
        <div className="bg-gray-800/50 border border-gray-700/30 rounded-xl px-4 py-3 mb-5">
          <p className="text-sm text-gray-300 leading-relaxed">{description}</p>
        </div>

        {/* Buttons */}
        <div className="flex gap-3">
          <button
            onClick={handleReject}
            className="flex-1 px-4 py-2.5 bg-gray-800 hover:bg-gray-700 text-gray-300 text-sm font-medium rounded-xl transition-colors"
          >
            Reject
          </button>
          <button
            onClick={handleApprove}
            className="flex-1 px-4 py-2.5 bg-green-600 hover:bg-green-500 text-white text-sm font-medium rounded-xl transition-colors shadow-[0_0_12px_rgba(34,197,94,0.3)]"
          >
            Approve
          </button>
        </div>
      </div>
    </div>
  );
}
