please create a python app to help do some automated tasks.

it would use image recognition to identify specific buttons or icons on the screen and perform actions like clicking, dragging, or refreshing based on the images provided.

check images in src/pngs:

money-count.png   this image represents the money count on the screen, need read the number from this image and store it in a variable.
click-to-buy.png  this is the target item, click to buy an item, has text: 完美投影仪， we can buy one pack with 3  完美投影仪 with 9 money unit
click-to-open-market.png this is the button to open the market menu.
click-to-open-sell-menu.png this is card we duplicated with 完美投影仪，click it to show the card info, in card info we can find the sell button.
click-to-refresh.png this is the button to refresh the store so we can get the target item to buy.

sell-button.png this is the button to sell the item to earn me money, can get text: 出售. one card can sell for 5 money unit.

target-to-drag.png this is 完美投影仪 we bought from store, we need drag it to the card slot to duplicate the card
target-to-drop.png this is the card slot where we need to drop the 完美投影仪 to duplicate it.

so the app should perform the following tasks:


 task1: buy 完美投影仪

1.Open the market menu by clicking on the "click-to-open-market.png" button.
2. click the refresh button "click-to-refresh.png" until the target item "click-to-buy.png" appears on the screen.
3. Once the target item appears, click on it to buy one pack of 完美投影仪, which contains 3 units and costs 9 money units.
4. Read the money count from "money-count.png" and store it in a variable to keep track of the remaining money.
5. refresh the store again to check if the target item is still available for purchase.
6. If the target item is still available, repeat steps 3-5 until the money is insufficient to buy another pack.

  task2: use the 完美投影仪 to duplicate cards

1. After buying the 完美投影仪, locate the item on the screen using "target-to-drag.png".
2. Drag the 完美投影仪 to the card slot represented by "target-to-drop.png" to duplicate the card. only do twice to avoid over-duplication.
3. then we click on the duplicated card to open its info using "click-to-open-sell-menu.png".
4. In the card info, locate the sell button using "sell-button.png" and click it to sell the duplicated card for 5 money units.
5. Repeat steps 3-4 for the second duplicated card to earn more money.

we need register a shortcut the start the task, and also a way to stop the task if needed.
and log all processes and actions taken by the app for debugging and tracking purposes.

you can create a html interface for testing.