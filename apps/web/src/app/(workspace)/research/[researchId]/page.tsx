import { ResearchShell } from "@/components/research/ResearchShell";

export default async function ResearchPage({
  params,
}: {
  params: Promise<{ researchId: string }>;
}) {
  const { researchId } = await params;
  return <ResearchShell researchId={researchId} />;
}
