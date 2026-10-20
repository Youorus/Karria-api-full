"""Le temps, vu comme une dépendance injectable.

Pourquoi ici et non dans chaque module
--------------------------------------
« Quelle heure est-il ? » n'a rien de métier : c'est du socle technique,
au même titre que la journalisation ou la pagination. Redéclarer le
protocole dans chaque module produirait N définitions identiques qui
finiraient par diverger — exactement le travers que la règle « ne jamais
dupliquer une source de vérité » proscrit.

Pourquoi un port plutôt qu'un appel direct
------------------------------------------
Appeler `datetime.now()` au fond d'un use case rend intestable toute règle
qui dépend de l'heure : on ne peut ni vérifier qu'une suppression horodate
`deleted_at`, ni exercer une expiration sans attendre. Injecter l'horloge
rend le temps observable, et les tests déterministes à minuit comme à midi.
"""

from karria.shared.clock.ports import Clock
from karria.shared.clock.system import SystemClock

__all__ = ["Clock", "SystemClock"]
