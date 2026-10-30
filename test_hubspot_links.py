import copy
import json
import tempfile
import unittest
from unittest.mock import patch
import hubspot_links as links
import partner_sources
import review

RAW='https://app.hubspot.com/contacts/24119306/record/0-1/259233623944/'
URL=RAW.rstrip('/')
BUTTON={'type':'button','text':'View contact in HubSpot','url':RAW}
MESSAGE={'type':'message','ts':'10.1','channel':'C','bot_id':'B','text':'PARTNERSHIP MQL DIGEST', 'attachments':[{'actions':[BUTTON]}]}
CFG=dict(review_enabled=True,review_bots={'B'},review_channels={'C'},team_id='T',app_id='A',routing_enabled=True)

class HubSpotLinks(unittest.TestCase):
    def test_legacy_and_block_kit_buttons_rich_links(self):
        for m in (MESSAGE,{'blocks':[{'type':'actions','elements':[BUTTON]}]},
                  {'blocks':[{'type':'section','accessory':BUTTON}]},
                  {'blocks':[{'elements':[{'elements':[{'type':'link','url':RAW}]}]}]}):
            self.assertEqual(links.extract(m),URL)
        self.assertEqual(links.extract({'text':'<'+RAW+'|View contact>'}),URL)
        self.assertEqual(links.canonical('https://app-eu1.hubspot.com/contacts/12/contact/34?foo=bar'),'https://app-eu1.hubspot.com/contacts/12/record/0-1/34')

    def test_wrong_hosts_paths_ambiguity_and_button_precedence(self):
        for bad in ('http://app.hubspot.com/contacts/1/contact/2','https://app.hubspot.com.evil.test/contacts/1/contact/2',
                    'https://evil@app.hubspot.com/contacts/1/contact/2','https://app.hubspot.com/contacts/1/record/0-3/2',
                    URL+'evil',URL+'<@BAD>'):
            self.assertIsNone(links.canonical(bad))
        other=dict(BUTTON,url='https://app.hubspot.com/contacts/1/contact/2')
        self.assertIsNone(links.extract({'actions':[BUTTON,other]}))
        self.assertEqual(links.extract({'text':other['url'],'actions':[BUTTON]}),URL)
        self.assertIsNone(links.source_url({'hubspot_url':None,'text':URL}))

    def test_saved_source_and_handoff_survive_flattening_and_truncation(self):
        body=dict(team_id='T',api_app_id='A',event_id='E',event=MESSAGE)
        event=review.accept_review(body,CFG)
        self.assertEqual(event['hubspot_url'],URL);self.assertNotIn(URL,event['text'])
        data=dict(company='Tensor44',email='test@example.test',score='2',route='Supply Team',signals={'blocker':'unverified'},contact='Gareth',decision='triage',checks={k:('unverified','') for k in ('identity','company','fit','potential')},summary='',why='',risks=[],next_step='Validate',drafts=[],sources=[])
        with tempfile.TemporaryDirectory() as d:
            ledger=review.Ledger(d+'/db');ledger.record(event['key'],event,dict(data,hubspot_url=URL),'ok')
            saved=json.loads(ledger.review(event['key'])['data'])
            self.assertEqual(saved['hubspot_url'],URL)
            handoff=partner_sources.handoff(saved,saved['source_event'],CFG,requested_route='Supply Team',requested_by=partner_sources.COLBY)
            self.assertIn('<'+URL+'|HubSpot contact>',handoff['text'])
        self.assertIn('<'+URL+'|View contact in HubSpot>',review.render(dict(data,hubspot_url=URL)))
        # Model output cannot substitute another CRM URL for the observed source.
        handoff=partner_sources.handoff(dict(data,hubspot_url='https://app.hubspot.com/contacts/9/contact/9'),event,CFG,requested_route='Supply Team',requested_by=partner_sources.COLBY)
        self.assertIn(URL,handoff['text']);self.assertNotIn('/contacts/9/',handoff['text'])

    @patch.dict('os.environ',{'SLACK_BOT_TOKEN':'test'})
    @patch('review.api')
    def test_manual_and_older_reviews_recover_verified_parent(self,api):
        api.return_value={'messages':[MESSAGE]}
        client=review.ReviewClients(dict(CFG,notion_lookup=False))
        event=dict(kind='review_manual',channel='C',thread_ts='10.1')
        client.parent(event);self.assertEqual(event['hubspot_url'],URL)
        data={'source_event':dict(kind='review',channel='C',thread_ts='10.1')}
        links.hydrate(data,client);self.assertEqual(data['hubspot_url'],URL)
        api.return_value={'messages':[dict(MESSAGE,bot_id='OTHER')]}
        bad={'source_event':dict(kind='review',channel='C',thread_ts='10.1')}
        links.hydrate(bad,client);self.assertIsNone(bad['hubspot_url'])

if __name__=='__main__':unittest.main()
