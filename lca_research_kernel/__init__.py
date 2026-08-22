"""D-18 TianGong-native LCA Research Kernel.

The historical ``ResearchProject`` factory facade is intentionally not a
package-level product export.  Legacy CLI, replay and Council code must import
``lca_research_kernel.project`` explicitly; the active product path uses the
small Product Interface and Project Record ports.
"""

__all__: list[str] = []
