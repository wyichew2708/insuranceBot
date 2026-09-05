"""What a question is asking for, and what would count as having answered it.

The seven verification gates are all provenance checks. They prove a figure
came from a named table row, that a cited page was read, that contact details
belong to the session's channel. Not one of them compares the answer to the
*question* — so an answer about travel-delay thresholds passes every gate when
the customer asked what the policy costs a year, because the thresholds really
did come from a page we really did load.

Measured on the real corpus, that is the single largest failure class: of 3,130
failing cases, 1,177 were answered when nothing in the corpus could answer
them. Not hallucinated — every figure still bound to a row — just fluent,
grounded, and about something else.

This module is the missing half. An intent says what kind of thing the customer
asked for; `Requirement` says what evidence would settle it. The gate that uses
them refuses when the evidence is absent rather than reaching for the nearest
page, and the same taxonomy tells the compiler which source is authoritative
for which question — a policy wording owns limits and exclusions and is largely
silent on eligibility, which is most of what customers actually ask.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class Intent(str, Enum):
    """What the customer wants, not what they typed."""

    limit = "limit"  # how much does it pay
    exclusion = "exclusion"  # what is not covered
    eligibility = "eligibility"  # can I buy it, am I old enough
    application = "application"  # how do I buy it
    claim = "claim"  # how do I claim, what do you need
    price = "price"  # what does it cost
    renewal = "renewal"  # when does it renew, can I cancel
    document = "document"  # send me the wording
    coverage = "coverage"  # what does it cover, broadly
    definition = "definition"  # what does <term> mean
    smalltalk = "smalltalk"  # hello, thanks, are you a bot
    browse = "browse"  # what do you sell, show me your life plans
    entity = "entity"  # who underwrites this, which legal entity
    offer = "offer"  # is there a promotion, a discount, cashback

    # --- the five the corpus can never settle ------------------------------
    # Not gaps in the wiki. A policy wording says what a claim requires; no
    # edition of it says where *your* claim got to. Left unclassified these
    # fell to `unknown`, which the answerability gate treats as unconstrained,
    # so the turn was answered from whatever page retrieval was holding —
    # 237 of 379 failing turns on the golden conversation dataset, and the
    # worst of them handed a phishing victim a link and told them to log in.
    claim_status = "claim_status"  # where is my claim, when will it pay
    servicing = "servicing"  # change my address, my plan, my beneficiary
    payment = "payment"  # can I pay monthly, when does my refund land
    account = "account"  # log in, reset my password
    contact = "contact"  # give me a human, I want to complain, is this a scam

    unknown = "unknown"


#: Openings and closings, and the two questions every chat surface is asked
#: before anything else: what are you, and what can you do.
#:
#: Anchored end to end on purpose. "hi" is a greeting; "hi, what does travel
#: insurance cover?" is a coverage question with a greeting attached, and
#: treating the second as smalltalk would drop a real question on the floor.
#: The trailing class allows the punctuation and emphasis people actually
#: type — "hello!!", "hi there…", "thanks :)".
SMALLTALK: dict[str, re.Pattern[str]] = {
    "greeting": re.compile(
        r"^(?:hi|hey|hello|hallo|helo|hiya|yo|sup|greetings|good\s+(?:morning|afternoon|evening|day)"
        r"|hi\s+there|hey\s+there|hello\s+there)$",
        re.I,
    ),
    "thanks": re.compile(
        r"^(?:thanks?|thank\s+you(?:\s+so\s+much)?|ty|thx|cheers|much\s+appreciated"
        r"|ok(?:ay)?|got\s+it|understood|noted|great|perfect|nice)$",
        re.I,
    ),
    "farewell": re.compile(r"^(?:bye|goodbye|good\s+bye|see\s+you|see\s*ya|cya|later|that.s\s+all)$", re.I),
    "capability": re.compile(
        r"^(?:help|what\s+can\s+you\s+do|what\s+do\s+you\s+do|how\s+can\s+you\s+help"
        r"|who\s+are\s+you|what\s+are\s+you|are\s+you\s+(?:a\s+)?(?:bot|robot|human|real|ai|person)"
        r"|how\s+(?:do|does)\s+(?:this|it)\s+work|what\s+is\s+this)$",
        re.I,
    ),
}
#: Punctuation and emphasis stripped before the patterns are tried.
_TRIM_RE = re.compile(r"^[\s\W_]+|[\s\W_]+$")


def smalltalk_kind(question: str) -> str | None:
    """Which pleasantry this is, or None if the turn asks something.

    A greeting is not a question the corpus can fail to answer. Routing one
    through retrieval produces "I could not establish that from our approved
    product pages" in reply to "hi", which reads as a broken bot rather than a
    careful one — and spends a retrieval, a model call and eight gates saying
    so.
    """
    trimmed = _TRIM_RE.sub("", " ".join(question.split()))
    if not trimmed or len(trimmed.split()) > 5:
        return None
    for kind, pattern in SMALLTALK.items():
        if pattern.match(trimmed):
            return kind
    return None


#: Someone shopping rather than asking. "what life products", "looking for a CI
#: plan", "do you have pet insurance" — the customer wants to know *what
#: exists*, and every one of these was answered from a single product page's
#: prose because retrieval is built to find the best page, not to report that
#: several are relevant. Measured on the real bundle: "what life products"
#: returned the Products Liability page, having matched on the word "products".
#: The noun a shopper names: the category, not a benefit.
_OFFERING = r"(?:products?|plans?|policies|policy|insurance|cover(?:age)?s?|options?)"

BROWSE_RE = re.compile(
    # "what life products", "which plans do you have" — at most two words of
    # qualifier between the interrogative and the noun, and then either the
    # question ends or a possession verb follows. The gap is what keeps "what
    # is not covered by fire insurance?" out: four words of question sit in it,
    # and that turn is *about* a product rather than a request to see the list.
    # The qualifier may not be a verb: "what life products" is shopping, and
    # "what is the coverage" is a question about one product that happens to
    # end on the same noun.
    rf"^(?:what|which)\s+(?:(?!(?:is|are|was|were|do|does|did|can|will)\b)\w+\s+){{0,2}}{_OFFERING}\s*"
    rf"(?:(?:do|does|are|can|have)\b[\w\s]{{0,20}})?[?.!]?$"
    # Explicit shopping language, anywhere in the turn.
    r"|\b(?:looking|search(?:ing)?|shopping)\s+for\b"
    r"|\b(?:show|list)\s+me\b"
    r"|\bdo\s+you\s+(?:have|sell|offer)\b"
    r"|\bwhat\s+(?:do|can)\s+you\s+(?:sell|offer|insure)\b"
    rf"|\b(?:any\s+other|what\s+other)\s+{_OFFERING}\b",
    re.I,
)

#: Ordered: the first match wins, so the specific patterns precede the broad
#: ones. `coverage` deliberately sits near the end — "what does X cover" is the
#: catch-all a dozen sharper questions would otherwise be swallowed by. And
#: `definition` precedes `limit` because "what does excess mean" is a request
#: for a definition that happens to name a limit word, not a request for a
#: number.
_PATTERNS: tuple[tuple[Intent, re.Pattern[str]], ...] = (
    # Before price: "who is the insurer" carries no price word, but "which
    # legal entity am I claiming against" carries "claim", and the claim
    # pattern would otherwise take it. Unclassified, these fell to `unknown`,
    # which the answerability gate treats as unconstrained — so "Who is the
    # insurer behind Etiqa Autolab Package Wic?" was answered with a fire-peril
    # clause and every gate passed. 14 of 79 unsafe cases on the real corpus.
    (
        Intent.entity,
        re.compile(
            r"\b(who (?:is|are) (?:the )?(?:insurer|underwriter|company|provider)|who underwrites"
            r"|underwritten by|which (?:legal )?(?:entity|company|insurer)|legal entity"
            r"|(?:insurer|underwriter) behind|who (?:do i|am i|would i) (?:claim|claiming) (?:against|from)"
            r"|who (?:backs|issues|stands behind))\b",
            re.I,
        ),
    ),
    # --- the five out-of-corpus intents, ahead of the topic patterns -------
    #
    # Each sits in front of the pattern that used to swallow it, and the
    # order between them is the order of how much damage a wrong reading
    # does. `contact` leads because the turns that reach it are the ones
    # where answering from a product page is worst: a fraud report, a
    # complaint, someone asking for a person. "I got an email asking me to
    # confirm my policy details" was answered "Please log in to TiqConnect to
    # update your details" — the corpus was quoted faithfully and the advice
    # was the attacker's.
    (
        Intent.contact,
        re.compile(
            # Deliberately not "how do I contact you". That is a question the
            # channel pages answer, it is answered correctly today, and
            # routing it would trade a good answer for a link.
            r"\b(speak|talk|put me through|connect me)\s+(?:to|with)\s+"
            r"(?:a\s+)?(?:someone|somebody|a\s+person|a\s+human|an?\s+agent|an?\s+adviser|staff)"
            r"|\b(?:real|actual|human)\s+(?:person|being|agent)\b"
            r"|\b(?:i want to |i.d like to |let me )?(?:make a )?complain(?:t|ts)?\b"
            r"|\b(?:operating|opening|office|business|working) hours\b|\bwhen are you open\b"
            r"|\bspeak to (?:a |an |the )?"
            r"(?:claims officer|financial adviser|financial advisor|adviser|advisor)\b"
            r"|\bnobody has helped\b|\bprovide feedback\b|\bgive feedback\b|\bbecome an agent\b"
            # Where the network is, not what it is: "what is a panel doctor" and
            # "does it have to be a panel hospital" are answered by the pages.
            r"|\bnearest (?:panel )?(?:clinic|hospital|workshop|branch)\b"
            r"|\b(?:list|directory) of (?:panel )?(?:clinics|hospitals|workshops)\b"
            r"|\bmedishield\b|\bcareshield\b|\beldershield\b"
            r"|\bdownload (?:your|the) app\b|\b(?:this )?chatbot\b"
            r"|\b(?:app|website|site|portal) (?:is |isn.t |is not |not )(?:working|loading|down)\b"
            r"|\bwithout my (?:permission|consent|knowledge)\b|\busing my identity\b"
            r"|\bidentity (?:theft|fraud)\b|\bthat i did not (?:submit|make|file|buy|purchase|authori[sz]e)\b"
            r"|\bcontact my (?:agent|adviser|advisor)\b"
            r"|\bescalate\b|\bspeak to (?:your )?(?:a )?(?:manager|supervisor)\b"
            # Fraud and phishing. A customer checking whether a message is
            # genuine must never be answered out of the corpus, whatever the
            # corpus happens to say about updating your details.
            # "fraud" only as something being reported or suspected. The bare
            # word read "does it cover credit card fraud?" and "what is covered
            # under Cyber Fraud?" as a customer in distress — coverage
            # questions on the cyber product, sent to a contact page.
            r"|\bphish(?:ing)?\b|\bscam(?:mer|med)?\b"
            r"|\bfraud(?:ulent)?\s+(?:e-?mail|sms|message|text|call(?:er)?|website|link|transaction|charge|claim)\b"
            r"|\b(?:report(?:ing)?|suspect(?:ed)?|victim of|targeted by)\b[\w\s]{0,30}\bfraud\b"
            r"|\b(?:is|was) (?:this|that|it)[\w\s]{0,12}\bfraud\b"
            r"|\b(?:is|was) (?:this|that|it) (?:email|sms|message|text|call|really|actually)"
            r" .{0,24}from you\b"
            r"|\b(?:really|actually) from (?:you|etiqa|tiq)\b"
            r"|\bdid you (?:send|email|text|call) me\b"
            r"|\bi (?:did ?n.t|never) (?:request|ask for)\b"
            # Reporting a message they were not expecting. "I got an email
            # asking me to confirm my policy details" is the canonical
            # phishing report, and the corpus answered it with "Please log in
            # to TiqConnect to update your details" — quoted accurately from a
            # real page, and exactly what the sender of that email wanted.
            r"|\bi (?:got|received|have had|had) (?:an?|this|some) "
            r"(?:e-?mail|sms|message|text|call|whatsapp|letter|otp)\b"
            r"|\bunauthorised|\bsomeone (?:used|accessed|hacked)\b"
            r"|\bclaiming to be\b"
            r"|\bsomeone (?:called|contacted|emailed|messaged|texted) me\b",
            re.I,
        ),
    ),
    (
        Intent.account,
        re.compile(
            r"\b(?:log|sign)\s?(?:in|on)\b|\blogin\b|\bpassword\b|\bmy account\b"
            r"|\b(?:customer|policy) portal\b|\btiqconnect\b|\blocked out\b"
            # The customer's own record: number, dates, status, people on it,
            # documents, agent, data. No product page has any of it.
            r"|\bmy policy number\b|\bpolicy number for\b"
            r"|\b(?:when|what date) (?:does|will|did) my (?:policy|cover|plan) "
            r"(?:expire|end|lapse)\b"
            r"|\bis my (?:policy|cover|plan) (?:still )?(?:active|in force|valid|live|renewed|lapsed)\b"
            r"|\bmy (?:policy|plan|cover) (?:expiry|expiration|renewal|start) date\b"
            r"|\b(?:beneficiary|nominee|limit|excess|tier) on my (?:policy|plan)\b|\bmy annual limit\b"
            r"|\b(?:save|finish|complete|continue|resume|cancel|amend|change) my application\b"
            r"|\bhaven.t (?:completed|finished) my application\b|\bwrong information on my application\b"
            r"|\b(?:did ?n.t|have ?n.t|never) (?:receive|received|get|got) (?:a |my |the )?"
            r"(?:confirmation|policy documents?|renewal notice|policy|certificate|receipt|invoice)\b"
            r"|\b(?:download|copy of|email me|send me) my "
            r"(?:policy|documents?|certificate|schedule|invoice)\b"
            r"|\bmy (?:certificate of insurance|tax invoice|policy schedule)\b"
            r"|\bmy policy document has\b|\bwhen will i (?:receive|get) my renewal notice\b"
            r"|\bwho is my (?:insurance )?(?:agent|adviser|advisor)\b"
            r"|\bchange my (?:agent|adviser|advisor)\b"
            # A request about the customer's own data record — a copy, consent —
            # not a question about how data is used, which the privacy page answers.
            r"|\bcopy of (?:the |my )?personal data\b|\bdata you hold on me\b"
            r"|\bwithdraw my consent\b|\bmarketing consent\b"
            r"|\b(?:on|in|with|for) my application\b|\bhas my (?:policy|plan|cover) been renewed\b"
            r"|\bwhy did my (?:policy|plan|cover) lapse\b|\bmy (?:policy|plan|cover) (?:has )?lapsed\b"
            # "Why do I need a medical examination?" is about the customer's own
            # application; "Will I need one for Term Life?" is a product question.
            r"|\bwhy do i need (?:a |to (?:go for |take |do )?)?medical "
            r"(?:examination|exam|check|check-up|underwriting)\b"
            r"|\b(?:keep|retain|store|hold) my (?:personal )?data\b"
            r"|\bnot receiving (?:the |my )?otp\b"
            r"|\botp\b[\w\s]{0,12}\bnot (?:coming|arriving|received)\b"
            r"|\b(?:reset|change) my (?:password|username|pin)\b",
            re.I,
        ),
    ),
    # Before `claim`, whose own pattern lists "claim status" — so
    # "what is my claim status" classified as a *procedure* question and was
    # answered with the claim-notification clause, which is a true sentence
    # and not the answer.
    (
        Intent.claim_status,
        re.compile(
            r"\b(?:claim|application|policy) status\b|\bstatus of (?:my|the) (?:claim|application|policy)\b"
            r"|\bwhere (?:is|.s) my (?:claim|application|refund|policy|money)\b"
            r"|\bwhere is (?:the|my) claim\b|\bmy claim (?:now|yet|still)\b"
            r"|\bhow (?:long|much longer) .{0,30}\b(?:take|takes|until|before) .{0,20}"
            r"(?:pay|paid|payout|settle|approv|process)"
            r"|\b(?:when|how soon) will .{0,30}\b(?:be )?(?:paid|pay out|payout|settled|approved|processed)\b"
            r"|\b(?:has|have) (?:my|the) (?:claim|application|refund|payment)[\w\s]{0,14}?been\b"
            r"|\bwhen will (?:my|the) [\w\s]{0,20}?reach (?:my|the) bank\b"
            r"|\b(?:is|are) (?:my|the) (?:claim|application|refund|payout|policy)"
            r" (?:approved|processed|ready|done|settled|paid|through)\b"
            r"|\bwhy (?:was|is|has) my (?:claim|application) (?:been )?"
            r"(?:rejected|declined|denied|delayed|taking so long)\b"
            r"|\bwhy (?:hasn.t|has not|isn.t) my (?:claim|application|refund|policy)\b"
            r"|\bappeal (?:a |the |my )?(?:rejected |declined )?claim\b"
            r"|\bappeal (?:a |the |your )?claim decision\b"
            r"|\bdisagree with (?:your|the) claim decision\b|\bamend a claim\b|\bforgot to attach\b"
            r"|\bhow much will i (?:receive|get) for my claim\b"
            r"|\bmy claim (?:was|is|has been) (?:rejected|declined|denied|approved|pending)\b"
            r"|\b(?:chase|chasing|follow up on) (?:my|the) claim\b"
            r"|\bstill (?:waiting|pending)\b",
            re.I,
        ),
    ),
    # Post-sale changes to a policy that exists. Nothing in a policy wording
    # can confirm that a change was made, or make one.
    (
        Intent.servicing,
        re.compile(
            r"\b(?:update|change|amend|correct|edit)\s+(?:my|the|our)\s+"
            r"(?:address|details|particulars|contact|email|phone|number|bank|nominee"
            r"|beneficiary|beneficiaries|name|plan|tier|sum insured|coverage|cover)\b"
            r"|\b(?:add|remove|change)\s+(?:a\s+)?(?:driver|rider|dependant|dependent|nominee|beneficiary)\b"
            r"|\bi (?:have )?(?:moved|relocated)\b|\bmoved (?:house|home|address)\b"
            r"|\bchange of (?:address|name|details|nominee|beneficiary)\b"
            r"|\b(?:upgrade|downgrade) my (?:plan|policy|cover|tier)\b"
            r"|\badd (?:my |our )?(?:wife|husband|spouse|partner|child|children|son|daughter"
            r"|employees?|staff|members?|dependants?|dependents?) to\b"
            r"|\b(?:increase|reduce|lower|raise) my (?:coverage|cover|sum insured|sum assured)\b"
            r"|\bcancel (?:just )?(?:one|a|the) rider\b"
            r"|\btransfer (?:my|the) policy\b",
            re.I,
        ),
    ),
    # Before `price` and before `limit`. "how much will I get back" carries
    # "get", which `limit` matches, so a refund question was being asked to
    # produce a benefit-table figure; and "can I pay monthly" carried nothing
    # any pattern matched at all.
    (
        Intent.payment,
        re.compile(
            r"\b(?:pay|paying|payment)\s+(?:for\s+)?(?:it\s+|this\s+|that\s+)?"
            r"(?:by|via|with|using|through|in)\s+"
            r"(?:instal?ments?|monthly|cash|card|giro|paynow|nets|medisave|cpf|srs|cheque)\b"
            r"|\bpay (?:it )?(?:monthly|yearly|annually|quarterly|in instal?ments?)\b"
            # NOT a bare "how do I pay". "How do I pay for Tiq Travel
            # Insurance?" is a question about buying a plan, the product page
            # answers it, and the dataset expects it answered — routing it
            # traded a good answer for a link. The forms that belong here name
            # a *schedule* or a *method*, and those are matched above.
            r"|\bwhy (?:is|was|did|has) my premium\b|\bcalculate my premium\b|\bhow much do i owe\b"
            r"|\bcharged (?:twice|the wrong amount|incorrectly)\b|\bdouble.charged\b"
            r"|\bpayment (?:method|mode|option|plan)s?\b"
            r"|\b(?:how |can |could |may )?i pay (?:my|the) [\w\s]{0,16}?"
            r"(?:premium|policy|bill|instal?ment)s?\b"
            # A method, whatever qualifier precedes it: "by credit card",
            # "with my Visa". The method word is what makes it an account
            # action; "pay for Tiq Travel Insurance" names no method and is a
            # question about buying, which the product page answers.
            r"|\b(?:can|could|may|do) i pay (?:by|via|with|using)\b"
            r"|\bchange how often i pay\b|\bhow often i pay\b"
            r"|\b(?:next )?premium (?:payment )?(?:is )?due\b"
            r"|\breceipt for (?:my |the )?(?:premium|payment)\b"
            # The customer's own money moving, or failing to
            r"|\bmy (?:payment|card|giro|direct debit)\b[\w\s]{0,16}"
            r"(?:fail|declin|bounce|reject|not going through)"
            r"|\bi made a payment\b|\bhow do i get a refund\b"
            r"|\b(?:why )?have ?n.?t i received my refund\b"
            r"|\binstal?ments?\b|\bgiro\b|\bpaynow\b"
            r"|\b(?:use|pay with|pay using) (?:my )?(?:cpf|medisave|srs)\b"
            # The refund half, and it is deliberately narrow. "What do I get
            # back?" after a cancelled *trip* is a trip-cancellation limit
            # and belongs to the benefit table; the same words after a
            # cancelled *policy* are a premium refund. A bare `get back` read
            # the first as the second and sent three real figure questions to
            # the portal, so only the forms that name a refund, or ask when
            # one lands, are read here. A bare "how much will I get back"
            # stays a limit question — the safe half of the ambiguity.
            r"|\brefund (?:status|date|timeline|amount)\b"
            r"|\bwhen (?:will|do|does|is) .{0,24}\brefunds?\b"
            r"|\bwhere .{0,12}\bmy refund\b"
            r"|\brefunds? (?:of|on|for) (?:my|the) (?:premium|policy|payment)\b"
            r"|\b(?:premium|money) back\b|\bpro-?rata refund\b"
            r"|\b(?:missed|late|failed|declined) (?:a )?payment\b|\bdirect debit\b"
            r"|\bwhen (?:is|was) (?:my|the) (?:premium|payment) due\b",
            re.I,
        ),
    ),
    (
        Intent.price,
        re.compile(
            # "Get me a quote", the fees and taxes on a premium, what moves it:
            # all price questions with no price in any document.
            r"\b(?:get|request|need|want|give) (?:me )?(?:a |an )?[\w\s]{0,20}?quote\b"
            r"|\bhow do i get (?:a |an )?[\w\s]{0,20}?quote\b"
            r"|\b(?:administrative|admin|processing|service) fees?\b|\bgst\b"
            r"|\bfactors? (?:that )?(?:affect|determine)s? (?:my |the )?premium\b"
            r"|\b(?:reduce|lower|cut) my premium\b|\bhigher premium\b"
            r"|\bpremium (?:is )?(?:calculated|computed|determined|worked out)\b|"
            r"\b(how much (does|do|is|would) .{0,40}(cost|premium)|what.{0,12}(the )?(premium|price|cost)"
            r"|cost me|per (year|month|annum)|how much to (buy|get)|cheap(er|est)?\b)",
            re.I,
        ),
    ),
    (
        Intent.renewal,
        re.compile(r"\b(renew(s|al|ed|ing)?|auto-renew|expire(s|d)?|cancel(led|lation)?|terminate)\b", re.I),
    ),
    (
        Intent.document,
        re.compile(
            r"\b(policy (document|wording|contract)|download|send me the|copy of (the|my) (policy|wording)"
            r"|certificate of insurance|product summary)\b",
            re.I,
        ),
    ),
    # Before claim and coverage: "is there a promo on travel cover" is a
    # question about the offer, and only a promotion page can answer it.
    (
        Intent.offer,
        # "discount" only when it is not the no-claim discount, which is a
        # policy figure: "what is the maximum NCD" is a limit question, and
        # reading it as an offer refused ten private-car cases for want of a
        # promotion page.
        re.compile(
            r"\b(promo|promotion|offer|deal|voucher|cashback|rebate|sale)s?\b"
            # "discount" in a turn that mentions claims anywhere — "I have
            # not claimed in years, how far can my discount go" — is the NCD.
            r"|^(?!.*\b(?:claim(?:s|ed|ing)?|ncd|no[- ]claims?)\b).*\bdiscounts?\b",
            re.I | re.S,
        ),
    ),
    (
        Intent.claim,
        re.compile(
            # Process language only. "I am claiming for wear and tear, will you
            # pay?" is asking whether something is covered, not how to lodge a
            # claim — reading it as process sent exclusion questions to a gate
            # that wanted a claims page cited.
            r"\b(how (?:(?:do|can) i |to )claim|make a claim|file a claim|submit a claim"
            r"|claim (process|procedure|form|status)"
            r"|what (do|documents) .{0,24}(need|submit|send).{0,24}claim)\b",
            re.I,
        ),
    ),
    (
        Intent.application,
        re.compile(
            r"\b(how (?:(?:do|can) i |to )(buy|purchase|apply|take out|sign up)"
            r"|steps to (buy|take out|apply)"
            r"|walk me through buying|where (do|can) i buy"
            # "Can I get Home Insurance from a broker?" asks which route sells
            # it, not whether the customer qualifies — without this it read as
            # eligibility and was refused for not discussing age or residency.
            r"|can i (get|buy|purchase|arrange) .{0,44}\b(from|through|via)\b"
            r"|where do i go to arrange"
            # "i want to buy cancer insurance" asks how to buy it; unread, it
            # was answered from the definitions page.
            r"|(?:i )?(?:want|would like|wish|looking) to (buy|purchase|apply for|sign up for|get) "
            r"|(?:buy|purchase) (?:a |the |an )?(?:policy|plan|cover|insurance)\b)\b",
            re.I,
        ),
    ),
    (
        Intent.eligibility,
        re.compile(
            r"\b(am i eligible|eligibilit|who is eligible|eligible (?:to|for)|who can (buy|apply|purchase)"
            r"|can i (buy|apply|purchase|get)|who qualifies|age requirements?|entry age"
            r"|do i qualify|age limit|more than \d+ years old|minimum age|maximum age"
            r"|can my (child|spouse|wife|husband|parent))\b",
            re.I,
        ),
    ),
    (
        Intent.exclusion,
        re.compile(
            r"\b(exclu(de|ded|sion|sions)|not cover(?:ed|s)?|won'?t (you )?(pay|cover)|is .{0,30} covered)\b",
            re.I,
        ),
    ),
    (
        Intent.definition,
        re.compile(r"\b(what (is|does) .{0,40}\bmean\b|what is meant by|define|explain what)\b", re.I),
    ),
    (
        Intent.limit,
        re.compile(
            # An interrogative frame is required, not the bare noun. "Tell me
            # about excess amount" is a customer exploring an alias, and
            # demanding a bound figure of it refused three legitimate questions
            # on the seed bundle. A limit question asks for an amount.
            # The article is required. "What is *the* excess on private car" asks
            # for a number; "what is excess amount and what does it give me" is a
            # customer meeting the word for the first time, and refusing that for
            # want of a figure is refusing a definition question.
            r"\b(what (is|are|s) (the|my|your|its) .{0,34}\b(limit|cap|excess|sub-?limit|threshold"
            r"|payout|percentage|discount)\b"
            r"|how much (does|do|is|are|will|can) .{0,34}(pay|cover|reimburse|claim|get|limit|excess)"
            r"|maximum (payout|amount|sum|percentage)|how long must|most .{0,20}will pay"
            r"|is there (a|an|any) .{0,24}(limit|cap|excess|sub-?limit))\b",
            re.I,
        ),
    ),
    # `coverages` is not a word most style guides would allow and is exactly
    # what customers type. The plural was missing, so "what's the coverages"
    # classified as unknown and the composer had no signal to prefer the cover
    # page over the exclusions page beside it.
    # After `limit`, and that placement is the whole of its safety. "how much
    # is it?" on turn three of a conversation about a product is a price
    # question and was classified `unknown` — 78 failing turns, the largest
    # single intent in the set. Written loosely enough to catch the bare
    # forms customers actually type, and placed where every limit reading has
    # already had its chance: "how much is the excess" is matched above and
    # never reaches here.
    (
        Intent.price,
        re.compile(
            # A bare "how much?" is deliberately NOT here. On turn three of
            # "does Corporate Travel pay for an 8-hour delay?" it asks for the
            # *benefit*, and reading it as a price question turned three
            # answered limit questions into refusals. The object separates
            # them: "how much is it" is about the plan; "how much" alone is
            # about whatever was last discussed, which is a limit.
            r"^\s*(?:and\s+|so\s+|ok(?:ay)?,?\s+|just\s+)?"
            r"(?:how much\s+is\s+(?:it|this|that|the plan|the policy|the cover(?:age)?)"
            r"|what.{0,4}s? the (?:price|cost|premium)|the (?:price|cost|premium)"
            r"|price|cost|premium)\s*(?:then|please|pls)?\s*[?.!]*\s*$"
            r"|\bhow much (?:is|would) (?:it|this|that|the (?:plan|policy|cover(?:age)?))\b"
            r"|\bjust the (?:price|cost|premium)\b|\bwhat about (?:the )?(?:price|cost)\b"
            r"|\bhow much .{0,20}\bfor (?:a|the) (?:year|month|policy)\b",
            re.I,
        ),
    ),
    (
        Intent.coverage,
        re.compile(r"\b(cover(s|ed|age|ages)?|include(s|d)?|protect(s|ion)?|benefit(s)?)\b", re.I),
    ),
)


def classify(question: str) -> Intent:
    """The intent of a question, or `unknown`.

    Deliberately lexical. A model could read intent better, but this decides
    whether a customer gets an answer or a handoff, and that decision has to be
    reproducible, free, and identical on every machine — the same reasons the
    verification gates are not model calls either.
    """
    text = (question or "").strip()
    if not text:
        return Intent.unknown
    # Before the topic patterns: "help" would otherwise read as an application
    # question, and "what is this" as a definition.
    if smalltalk_kind(text):
        return Intent.smalltalk
    # Before the topic patterns, and after smalltalk. "what plans do you have"
    # would otherwise be read as coverage and answered from whichever single
    # page scored highest.
    if BROWSE_RE.search(text):
        return Intent.browse
    for intent, pattern in _PATTERNS:
        if pattern.search(text):
            return intent
    return Intent.unknown


#: Intents no edition of the corpus can settle, whatever it holds.
#:
#: Every other intent is a question about a *product*, and a better corpus
#: would answer it. These are questions about a *customer* — their claim,
#: their account, their money, their need for a person — and no policy
#: document has ever contained the answer. The distinction is not a
#: confidence threshold: it is the difference between "we could not find it"
#: and "it is not the kind of thing that is written down here".
#:
#: Two things read this set. Retrieval is skipped for them, so the turn costs
#: no pages and no model call. And `gate_answerability` fails them outright,
#: so a draft that reached the composer by some other path is still refused
#: rather than delivered — the routing is the useful behaviour, the gate is
#: the guarantee.
OUT_OF_CORPUS: frozenset[Intent] = frozenset(
    {
        Intent.claim_status,
        Intent.servicing,
        Intent.payment,
        Intent.account,
        Intent.contact,
    }
)


def classify_topic(text: str) -> Intent:
    """What a piece of *corpus* text is about — a heading, a section title.

    `classify` reads a customer's question and decides, among other things,
    whether any document could answer it: five of its intents mean "no policy
    document has this" and are routed before retrieval. That reading is wrong
    for text that came *out of* a document. A FAQ heading titled "When will
    GIRO deductions be made for the renewal premium?" is not an out-of-corpus
    question — it is corpus, and the paragraph under it is the answer. Read
    with `classify` it becomes `payment`; read as a topic it is `renewal`,
    which is what the composer matching it against a renewal question needs.

    Measured when the split was introduced: 33 of 467 FAQ headings changed
    intent under `classify`, six of them away from a real topic, and
    `faq_pick` — which counts headings sharing the question's intent to
    decide how strict to be — picked a different entry on three cancer
    insurance turns and lost all three to the entitlement gate. This function
    is `classify` with the out-of-corpus patterns skipped, and on every FAQ
    heading in the real corpus it agrees with what `classify` said before the
    split existed.
    """
    text = (text or "").strip()
    if not text:
        return Intent.unknown
    if smalltalk_kind(text):
        return Intent.smalltalk
    if BROWSE_RE.search(text):
        return Intent.browse
    for intent, pattern in _PATTERNS:
        if intent in OUT_OF_CORPUS:
            continue
        if pattern.search(text):
            return intent
    return Intent.unknown


@dataclass(frozen=True)
class Requirement:
    """What an answer must show before it counts as having answered."""

    #: A figure bound to a benefit-table row or an SOR field.
    needs_figure: bool = False
    #: ...and labelled as one of these. Without it, *any* figure satisfies the
    #: clause: "how much does travel insurance cost a year" passed on a `$350`
    #: quoted out of a policy wording about lost passports. A price question is
    #: settled by a premium, not by a number.
    needs_figure_label: tuple[str, ...] = ()
    #: A cited page whose id ends in one of these.
    needs_page_suffix: tuple[str, ...] = ()
    #: A cited page of one of these types.
    needs_page_type: tuple[str, ...] = ()
    #: Words the answer must contain to be about the right subject at all.
    needs_any_term: tuple[str, ...] = ()
    #: A rendered purchase route — the product's deep link and hotline for the
    #: session's channel. Structural rather than lexical, and that is the
    #: point: "how do I buy" is answered by a route, and no policy clause can
    #: accidentally satisfy it the way the word "purchased" once did.
    needs_channel_route: bool = False
    #: Page-id suffixes that *hold* the answer, as opposed to `needs_page_suffix`
    #: which says what a cited page must look like for the gate to be satisfied.
    #:
    #: The difference is the direction. Everything else on this class is read
    #: after an answer is written, to reject it. This is read before, to go and
    #: fetch what the question needs — which is the gap that let "how to buy"
    #: be answered from three FAQ entries that happened to repeat the word
    #: "buy", while the product's own "How to buy" section sat unread on a page
    #: that was already loaded.
    holds_answer: tuple[str, ...] = ()

    #: Whether naming what could not be established counts as answering.
    #:
    #: For a limit it does. An anonymous customer asking the medical expenses
    #: limit cannot be given one — it varies by plan tier — and the product
    #: decision is to say so and invite them to sign in, which teaches them
    #: something a flat refusal does not. That is the difference between "I
    #: don't know" and "I know why I can't tell you yet".
    #:
    #: For a price it does not. Nothing in the corpus carries a premium, so an
    #: unresolved marker there is not an explanation, just an absence.
    satisfied_by_unresolved: bool = False

    @property
    def checkable(self) -> bool:
        """Does this requirement demand anything of an answer?

        `holds_answer` steers retrieval and asks nothing of the result, so an
        entry carrying only that must not make the gate refuse. `coverage` and
        `definition` are in the table for steering alone and are deliberately
        unconstrained — refusing a customer for asking broadly is worse than
        answering them broadly. Adding them without this property failed about
        a hundred generated cases with "asked for coverage; the answer shows
        none of it".
        """
        return bool(
            self.needs_figure
            or self.needs_page_suffix
            or self.needs_page_type
            or self.needs_any_term
            or self.needs_channel_route
        )


#: What each intent demands. Intents absent from this table are unconstrained —
#: `coverage`, `definition` and `unknown` are answerable from ordinary prose and
#: demanding structure of them would refuse customers for asking broadly.
REQUIREMENTS: dict[Intent, Requirement] = {
    Intent.limit: Requirement(needs_figure=True, satisfied_by_unresolved=True, holds_answer=("/benefits",)),
    Intent.exclusion: Requirement(
        needs_page_suffix=("/exclusions",),
        needs_any_term=("exclud", "not covered", "exclusion"),
        holds_answer=("/exclusions",),
    ),
    # `needs_any_term` on a procedural intent is the same hole `price` had.
    # The clauses are OR'd, so a bare word satisfies the gate: "how to buy?"
    # was answered with 467 words of travel cover because one clause said the
    # Trip was "purchased from a registered Travel Agent", and "purchase" was
    # on the list. A procedure is not a word; it is an instruction, and these
    # are the forms an answer that actually gives one uses.
    Intent.claim: Requirement(
        needs_page_type=("journey",),
        needs_page_suffix=("/claims",),
        needs_any_term=("to make a claim", "to claim", "notify us", "submit your claim", "report the"),
        holds_answer=("/claims",),
    ),
    Intent.application: Requirement(
        needs_channel_route=True,
        needs_page_type=("journey", "channel"),
        needs_any_term=(
            "you can buy",
            "to buy",
            "buy online",
            "purchase online",
            "apply online",
            "start an application",
            "get a quote",
            "how to buy",
        ),
        # The product page's own "How to buy" section carries the channel
        # table; the empty suffix means the product page itself.
        holds_answer=("",),
    ),
    Intent.eligibility: Requirement(
        needs_any_term=("eligib", "age", "resident", "citizen", "pass holder", "qualify", "who can"),
        holds_answer=("/eligibility",),
    ),
    Intent.definition: Requirement(holds_answer=("/definitions",)),
    # `coverage` deliberately steers nothing. It is the catch-all a dozen
    # sharper questions fall into — "are wear and tear covered?" classifies
    # here and wants the *exclusions* page — so naming a page for it sends the
    # narrow cases to the wrong one. The open form ("what does it cover") is
    # steered in the composer, where the phrasing is still visible.
    # Nothing in this corpus carries a premium, a renewal date or a downloadable
    # document. These are not gaps to paper over with the nearest page — they
    # are the questions where improvising is most convincing and most costly.
    # A premium is a figure, and this corpus carries none — so requiring the
    # *word* let "We will indemnify the Insured Person(s) for cost incurred up
    # to the limit..." answer "how much does travel insurance cost a year".
    # The word appeared; the price did not. Requiring a bound figure, and
    # refusing to accept an unresolved marker in its place, makes the refusal
    # honest: there is no premium here to fetch.
    # No `needs_any_term` here, and the absence is the point. The clauses of a
    # Requirement are OR'd — any one satisfies the gate — so listing the word
    # "premium" reopened the hole the figure requirement closed: "What is the
    # premium for life insurance?" was answered with "If Your Age, gender,
    # smoker status ... is not correctly stated such that the Premium paid is
    # wrong, We reserve the rights to adjust" — a clause *about* premiums,
    # containing the word, containing no price. Every gate passed. On a
    # 1,000-case sample this was the single largest unsafe class. A price
    # question is settled by a bound premium figure or by the honest shortfall
    # ("premiums are not published in the documents I answer from"), and by
    # nothing in between.
    Intent.price: Requirement(
        needs_figure=True,
        needs_figure_label=("premium", "price", "cost"),
    ),
    # A backstop behind the deterministic entity answer in `api.entity`: if a
    # bundle declares no single underwriter and the turn reaches the composer,
    # the answer must at least cite the entity page. A product's own conditions
    # page contains the word "insurer" a hundred times and says nothing about
    # who that is.
    Intent.entity: Requirement(needs_page_type=("entity",)),
    # An offer is stated on a promotion page or nowhere. A policy clause about
    # "any other insurance covering the same damage" answered "is there a
    # promotion for home insurance" before this.
    Intent.offer: Requirement(needs_page_type=("promotion",)),
    Intent.renewal: Requirement(
        needs_any_term=("renew", "expire", "cancel", "free-look", "cooling"),
        holds_answer=("/conditions",),
    ),
    # "contract" and "premium" appear in almost every wording, so the loose
    # form passed "send me the policy wording" on an answer about renewal
    # notices. These are the words an answer that actually points at a document
    # would use.
    Intent.document: Requirement(
        needs_any_term=("policy wording", "product summary", "download", "policy document")
    ),
}
