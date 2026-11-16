import pytest
from app.services.dpr_agentic.financial_schema import ProjectFinancials, financial_extraction_completeness
from app.services.dpr_agentic.financial_extraction import extract_financial_sections, merge_section_financials
from app.services.dpr_agentic.schemas import SourcePage, AnalysisResult
from app.services.dpr_agentic.cam_adapter import build_snapshot_from_analysis
from app.services.dpr_agentic.context_router import select_context
from app.services.dpr_agentic.gemini import FINANCIAL_GROUP_SCHEMAS

PAGES=[SourcePage(page=1,text='Project cost machinery 33.00 lakh. QUOTATION total 33.00 lakh'),SourcePage(page=2,text='Means of Finance Bank loan 26 lakh and promoter contribution 7 lakh'),SourcePage(page=3,text='Repayment schedule Year 1 principal 3 lakh interest 2 lakh, Year 2 principal 3 lakh interest 1.7 lakh'),SourcePage(page=4,text='Profit and Loss Year 1 revenue 24 lakh net profit 4 lakh depreciation 2 lakh'),SourcePage(page=5,text='MR. KISHORE NAGAR applicant for custom hiring centre'),SourcePage(page=6,text='Page 6 detail MR. ANIL KUMAR borrower erroneously repeated in schedule')]
class Fake:
 def __init__(self):self.calls=[]
 async def extract_group(self,prompt,ctx,group):
  self.calls.append((group,[p['page'] for p in ctx['dpr']['pages']]))
  if group=='cost_funding':
   return ProjectFinancials(cost_of_project_stated_total=33,means_of_finance_components=[{'label':'Bank term loan','amount':26},{'label':'Promoter margin','amount':7}],term_loan_amount={'value':26,'page':2,'status':'extracted'})
  if group=='repayment':
   return ProjectFinancials(repayment_table=[{'year':1,'principal_due':{'value':3,'page':3,'status':'extracted'},'interest_due':{'value':2,'page':3,'status':'extracted'}}])
  return ProjectFinancials(years=[{'year':1,'revenue':24,'net_profit':4,'depreciation':2}])

@pytest.mark.asyncio
async def test_grouping_and_cam():
 llm=Fake(); result,audit=await extract_financial_sections(llm,PAGES,{})
 # unit_economics is optional: attempted once because operations/revenue
 # pages exist, never retried.
 assert {x[0] for x in llm.calls}=={'cost_funding','repayment','operating_financials','unit_economics'}
 assert len(llm.calls)==4, llm.calls
 assert financial_extraction_completeness(result)['missing_sections']==[]
 snap=build_snapshot_from_analysis(AnalysisResult(service_request_id='test',wallet_document_id='doc',document_sha256='a'*64,status='completed',agent_outputs={},findings=[],unresolved_questions=[],extracted_financials=result.model_dump(mode='json')),report_id='rep',report_version=1,analysis_run_id='r')
 assert snap.project_cost.dpr_reported_total.amount==33
 assert snap.means_of_finance.requested_loan.amount==26
 assert snap.means_of_finance.promoter_contribution.amount==7
 assert snap.repayment_schedule[0].total_debt_service.amount==5

@pytest.mark.asyncio
async def test_empty_group_is_not_a_success():
 class Empty:
  def __init__(self):self.calls=[]
  async def extract_group(self,prompt,ctx,group):
   self.calls.append(group);return ProjectFinancials()
 llm=Empty(); got,audit=await extract_financial_sections(llm,PAGES,{})
 assert not financial_extraction_completeness(got)['has_financial_data']
 assert set(audit.empty)=={'cost_funding','repayment','operating_financials'}
 # 3 core domains + optional unit_economics + max 2 bounded recoveries;
 # an empty optional group is neither retried nor reported empty.
 assert len(llm.calls)==6


def test_complementary_year_data_merges():
 a=ProjectFinancials(years=[{'year':1,'revenue':24}])
 b=ProjectFinancials(years=[{'year':1,'principal_repayment':3,'interest_repayment':2}])
 merged=merge_section_financials(a,b)
 assert len(merged.years)==1
 assert merged.years[0].revenue==24
 assert merged.years[0].principal_repayment==3


def test_response_schemas_are_small():
 for group,schema in FINANCIAL_GROUP_SCHEMAS.items():
  assert len(str(schema)) < 4500,(group,len(str(schema)))
  # cost_funding is 9: its 7 original fields plus the two fund-flow totals
  # (scalar {value,page,status} objects). The size cap above is the real
  # guard against a return to the monolithic schema.
  assert len(schema['properties'])<=10


def test_cost_funding_schema_requests_the_fund_flow_totals():
 props=FINANCIAL_GROUP_SCHEMAS['cost_funding']['properties']
 assert 'fund_flow_sources_total' in props
 assert 'fund_flow_applications_total' in props


def test_document_context_includes_applicant_conflict():
 from app.services.dpr_agentic.schemas import AnalysisInput
 inp=AnalysisInput(service_request_id='test',wallet_document_id='doc',document_sha256='a'*64,pages=PAGES)
 ctx=select_context('document',inp,{},[])
 combined=str(ctx['dpr']['identity_mentions'])
 assert 'KISHORE' in combined and 'ANIL' in combined


def test_verifier_receives_bottom_quote_excerpt():
 from app.services.dpr_agentic.schemas import AnalysisInput
 text='generic text'*400+'The special discrepancy is INR 33.00 lakh'+'more text'*300
 inp=AnalysisInput(service_request_id='test',wallet_document_id='doc',document_sha256='a'*64,pages=[SourcePage(page=20,text=text)])
 prior={'finance':{'findings':[{'category':'cost','severity':'high','title':'cost','explanation':'','evidence_page':20,'evidence_quote':'The special discrepancy is INR 33.00 lakh'}]}}
 ctx=select_context('verifier',inp,prior,[])
 assert 'The special discrepancy' in str(ctx['dpr']['source_pages'])

@pytest.mark.asyncio
async def test_grouped_gemini_http_request_has_small_schema_and_low_thinking():
 import httpx
 from app.services.dpr_agentic.gemini import GeminiAgentClient, CURRENT_ROLE
 requests=[]
 def handler(request):
  import json
  data=json.loads(request.content)
  requests.append(data)
  assert data['generationConfig']['thinkingConfig']=={'thinkingLevel':'LOW'}
  # 4,096 visible-answer cap + 4,096 LOW thinking allowance (Gemini 3
  # counts thinking against maxOutputTokens).
  assert data['generationConfig']['maxOutputTokens'] == 8192
  assert list(data['generationConfig']['responseSchema']['properties'])
  return httpx.Response(200,json={'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':'{"cost_of_project_stated_total":33.0}'}]}}]})
 async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
  gemini=GeminiAgentClient(client,'fake-key','gemini-3.8-flash',max_output_tokens=6144)
  token=CURRENT_ROLE.set('financial_cost_funding')
  try:
   result=await gemini.extract_group('extract cost',{'dpr':{'pages':[{'page':1,'text':'Total project cost 33 lakh'}]}},'cost_funding')
  finally:
   CURRENT_ROLE.reset(token)
 assert result.cost_of_project_stated_total==33
 assert len(requests)==1


def test_finance_section_prefers_schedule_annexure():
 from app.services.dpr_agentic.context_router import select_evidence_pages
 pages=[SourcePage(page=1,text='Repayment of term loan discussed in project introduction'),
        SourcePage(page=20,text='Annexure - Repayment Schedule\nYear 1 Principal 3.0 Interest 2.0\nYear 2 Principal 3.0 Interest 1.7')]
 selected=select_evidence_pages(pages,{'repayment_schedule'},limit=1)
 assert selected[0].page==20


def test_six_page_cam_with_many_findings():
 import fitz
 from app.services.dpr_agentic.schemas import Finding
 from app.services.dpr_agentic.cam_render import render_cam_report_pdf
 findings=[Finding(category='cost' if i%2 else 'capacity',severity='critical' if i<5 else 'high',
         title='Different financing and machine operation discrepancy '+str(i),
         explanation='DPR assumptions require evidence before final appraisal. '*5,
         suggested_action='Reconcile the stated figures and project assumptions.',evidence_page=i+1)
         for i in range(55)]
 report=AnalysisResult(service_request_id='test',wallet_document_id='doc',document_sha256='a'*64,
                       status='completed',agent_outputs={},findings=findings,unresolved_questions=[])
 snap=build_snapshot_from_analysis(report,report_id='stress',report_version=1,analysis_run_id='run')
 # The six-page invariant is the 1.x layout's; 2.x pages flow.
 snap=snap.model_copy(update={'schema_version':'1.0.0'})
 data=render_cam_report_pdf(snap)
 assert len(fitz.open(stream=data,filetype='pdf'))==6


def test_aif_claim_not_capital_subsidy():
 from app.services.dpr_agentic.cam_render import build_page3_context
 from app.services.dpr_agentic.cam_snapshot import ProjectProfile
 snap=build_snapshot_from_analysis(AnalysisResult(service_request_id='t',wallet_document_id='d',
                     document_sha256='a'*64,status='completed',agent_outputs={},findings=[],unresolved_questions=[]),
                     report_id='r',report_version=1,analysis_run_id='t',
                     project_profile=ProjectProfile(scheme_claimed='Agriculture Infrastructure Fund (AIF)'))
 ctx=build_page3_context(snap)
 assert ctx['subsidy_label']=='Not independently verified'
 assert ctx['claimed_scheme']=='Agriculture Infrastructure Fund (AIF)'

def test_same_harvest_capacity_issue_consolidates_but_distinct_cost_issue_does_not():
 from app.services.dpr_agentic.schemas import Finding
 from app.services.dpr_agentic.finding_consolidation import consolidate
 titles=['Seasonality Bottleneck and Mismatch Between Annual Acreage and Daily Throughput',
         'Acreage-Throughput Mismatch vs Agro-Climatic Harvest Windows',
         'Acreage Throughput Incompatibility with Local Harvest Seasonality']
 findings=[Finding(category='sector',severity='critical',title=t,
           explanation='The expected acres are not feasible within harvest window.',
           suggested_action='Recalculate capacity.',evidence_page=5) for t in titles]
 findings.append(Finding(category='finance',severity='high',title='Interest expense omitted',
           explanation='Cost statement misses financing costs.',suggested_action='Reconcile.',evidence_page=5))
 result=consolidate(findings)
 assert len(result)==2
 assert result[0].merged_duplicate_count==3
 assert len(consolidate(result))==2

@pytest.mark.asyncio
async def test_incomplete_extraction_skips_financial_agents_instead_of_spending_65k_tokens():
 from app.services.dpr_agentic.parallel_graph import analyze_parallel
 from app.services.dpr_agentic.schemas import AnalysisInput,AgentOutput
 class NoFinancials:
  model='gemini-3.8-flash'
  def __init__(self):self.calls=[]
  async def extract_group(self,prompt,ctx,group):
   self.calls.append('financial_'+group)
   return ProjectFinancials()
  async def generate(self,prompt,ctx):
   from app.services.dpr_agentic.gemini import CURRENT_ROLE
   self.calls.append(CURRENT_ROLE.get())
   return AgentOutput(summary='Inadequate source data',findings=[],unresolved_questions=[])
 llm=NoFinancials()
 inp=AnalysisInput(service_request_id='t',wallet_document_id='doc',document_sha256='a'*64,pages=PAGES)
 result=await analyze_parallel(inp,llm)
 assert result.status=='completed'
 assert result.extracted_financials is None
 assert list(result.agent_outputs)==['document','sector']
 assert all(x not in llm.calls for x in ('finance','risk','verifier','correction'))
 assert any('FINANCIAL_EXTRACTION_INCOMPLETE' in s for s in result.unresolved_questions)
 snap=build_snapshot_from_analysis(result,report_id='ref',report_version=1,analysis_run_id='run')
 assert snap.readiness_assessment.status.value == 'INSUFFICIENT_INFORMATION'
