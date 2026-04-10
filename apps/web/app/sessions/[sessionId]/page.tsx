import { AnnotationWorkspace } from "@/components/annotation/annotation-workspace";

export default async function SessionPage({ params }: { params: Promise<{ sessionId: string }> }) {
  const { sessionId } = await params;
  return (
    <div className="h-full">
      <AnnotationWorkspace sessionId={sessionId} />
    </div>
  );
}
