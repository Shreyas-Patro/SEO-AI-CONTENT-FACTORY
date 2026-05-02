from db.artifacts import list_pipeline_runs
from agents.content_architect import ContentArchitectAgent
from agents.faq_architect import FAQArchitectAgent

runs = list_pipeline_runs(limit=1)
rid = runs[0]['id']

arch = ContentArchitectAgent(rid)
arch_out = arch.run({'topic': 'Hosa Road'})

assert arch_out['articles_created'] >= 5, f"only {arch_out['articles_created']} articles"
print('Architect OK —', arch_out['articles_created'], 'articles')

faq = FAQArchitectAgent(rid, cluster_id=arch_out['cluster_id'])
faq_out = faq.run({'topic': 'Hosa Road', 'cluster_id': arch_out['cluster_id']})

coverage = faq_out.get('coverage_pct', 0)
assert coverage >= 80, f"only {coverage}% FAQ coverage"

print('FAQ OK —', faq_out['total_faqs'], 'FAQs,', coverage, '% coverage')