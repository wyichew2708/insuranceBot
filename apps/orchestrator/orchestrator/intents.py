"""Servicing intent taxonomy (§8.1.2).

Seeded from the Policy Services request types + claim types; the CMS bundle
carries one procedure block per intent (matched by search, tagged by intent
in block tags). Extend this list as the CMS publishes more procedures —
the classifier schema is generated from it, so unknown intents cannot leak
out of the model.
"""

from __future__ import annotations

CLAIM_INTENTS = [
    "claim-travel",
    "claim-motor-accident",
    "claim-motor-windscreen",
    "claim-home",
    "claim-personal-accident",
    "claim-hospitalisation",
    "claim-maid",
    "claim-life",
    "claim-critical-illness",
    "claim-death",
    "claim-others",
]

SERVICING_INTENTS = [
    "cancel-policy",
    "renew-policy",
    "update-address",
    "update-contact-details",
    "update-bank-details",
    "update-payment-method",
    "giro-setup",
    "credit-card-update",
    "change-nomination",
    "policy-loan",
    "surrender-policy",
    "reinstate-policy",
    "request-policy-document",
    "request-premium-statement",
    "change-sum-insured",
    "add-named-driver",
    "change-vehicle",
    "extend-coverage",
    "refund-request",
    "claim-status",
]

ALL_INTENTS = SERVICING_INTENTS + CLAIM_INTENTS

CLASSIFIER_CONFIDENCE_THRESHOLD = 0.8
