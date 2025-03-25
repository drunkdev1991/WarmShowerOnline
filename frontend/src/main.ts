import Alpine, { AlpineComponent } from "alpinejs"

// @ts-ignore
window.Alpine = Alpine

const API_URL = "http://localhost:5000"

const fetchApi = async (url: string, options?: RequestInit) => {
	return await fetch(`${ API_URL }${ url }`, {
		headers: { "Content-Type": "application/json" },
		...options,
	})
}

function warmShowerApp (): AlpineComponent<any> {
	return {
		// Reactive Data
		sessionCode: "",
		createdSession: {},
		joinCode: "",
		displayName: "",
		participantId: null,
		joinedSession: false,

		participants: {},
		messagesSent: {},
		incomingMessages: [],

		// ====== CREATE SESSION ======
		async createSession () {
			console.log("[Client] createSession() called")
			try {
				const res = await fetchApi("/api/session", { method: "POST" })
				const data = await res.json()
				console.log("[Client] Session created:", data)
				this.createdSession = data
				alert(`Session created! Code: ${ data.code }`)
			} catch (err) {
				console.error("[Client] Error in createSession:", err)
			}
		},

		// ====== JOIN SESSION ======
		async join () {
			console.log("[Client] join() called with joinCode =", this.joinCode, " displayName =", this.displayName)
			if (!this.joinCode) {
				alert("Please enter a session code.")
				return
			}
			try {
				const joinRes = await fetchApi(`/api/session/${ this.joinCode }/join`, {
					method: "POST",
					headers: { "Content-Type": "application/json" },
					body: JSON.stringify({ displayName: this.displayName }),
				})
				if (!joinRes.ok) {
					alert("Session not found or expired.")
					return
				}
				const joinData = await joinRes.json()
				console.log("[Client] Join success =>", joinData)
				this.participantId = joinData.participantId

				this.sessionCode = this.joinCode
				this.joinedSession = true

				// Fetch participants
				await this.fetchParticipants()

				// Initialize messagesSent
				for (const pid in this.participants) {
					if (pid !== this.participantId) {
						this.messagesSent[pid] = false
					}
				}

				// Start SSE
				this.startSSE()

				// Poll participants periodically
				setInterval(() => this.pollParticipants(), 5000)

			} catch (error) {
				console.error("[Client] Error in join():", error)
			}
		},

		// ====== FETCH PARTICIPANTS ======
		async fetchParticipants () {
			console.log("[Client] fetchParticipants() called, sessionCode =", this.sessionCode)
			try {
				const res = await fetchApi(`/api/session/${ this.sessionCode }/participants`)
				if (!res.ok) {
					console.warn("[Client] fetchParticipants() => session not found/expired?")
					return
				}
				const data = await res.json()
				console.log("[Client] participants received =>", data)
				this.participants = data.participants
			} catch (error) {
				console.error("[Client] Error in fetchParticipants():", error)
			}
		},

		// ====== POLL PARTICIPANTS ======
		async pollParticipants () {
			console.log("[Client] pollParticipants() called")
			await this.fetchParticipants()
			// If new participants joined, ensure we set messagesSent to false
			for (const pid in this.participants) {
				if (pid !== this.participantId && this.messagesSent[pid] === undefined) {
					this.messagesSent[pid] = false
				}
			}
		},

		// ====== START SSE ======
		startSSE () {
			console.log("[Client] startSSE() => create EventSource for participantId=", this.participantId)
			const url = `/api/session/${ this.sessionCode }/stream/${ this.participantId }`
			const evtSource = new EventSource(url)

			evtSource.onmessage = (event) => {
				console.log("[Client] SSE onmessage =>", event.data)
				const msg = JSON.parse(event.data)
				this.incomingMessages.push(msg)
			}

			evtSource.onerror = (err) => {
				console.warn("[Client] SSE onerror:", err)
			}
		},

		// ====== SEND MESSAGE ======
		async sendMessageTo (targetPid: string) {
			console.log("[Client] sendMessageTo() => targetPid =", targetPid)
			if (this.messagesSent[targetPid]) {
				alert("You have already messaged this participant.")
				return
			}
			const msgText = prompt(`Type your warm shower message for ${ this.participants[targetPid] }:`, "")
			if (!msgText) {
				console.log("[Client] sendMessageTo() => user canceled prompt")
				return
			}
			try {
				const res = await fetchApi(`/api/session/${ this.sessionCode }/message`, {
					method: "POST",
					headers: { "Content-Type": "application/json" },
					body: JSON.stringify({
						from: this.participantId,
						to: targetPid,
						text: msgText,
					}),
				})
				if (!res.ok) {
					const errData = await res.json()
					console.warn("[Client] sendMessageTo() => error from server:", errData)
					alert("Error sending message: " + (errData.error || "Unknown"))
					return
				}
				console.log("[Client] Message successfully sent to", targetPid)
				this.messagesSent[targetPid] = true
				alert(`Message sent anonymously to ${ this.participants[targetPid] }`)

				this.checkIfDone()
			} catch (error) {
				console.error("[Client] Error in sendMessageTo():", error)
			}
		},

		// ====== CHECK COMPLETION ======
		checkIfDone () {
			console.log("[Client] checkIfDone() called")
			const allPids = Object.keys(this.participants).filter(pid => pid !== this.participantId)
			const totalOthers = allPids.length
			const sentCount = allPids.filter(pid => !!this.messagesSent[pid]).length
			console.log(`[Client] SentCount=${ sentCount }, totalOthers=${ totalOthers }`)
			if (sentCount >= totalOthers && totalOthers > 0) {
				alert("Thank you for participation! You have messaged everyone.")
			}
		},

				// ====== END SESSION ======
		async endSession () {
			console.log("[Client] endSession() called");


			// Show confirmation prompt
			const userInput = window.prompt("CAUTION! Typing 'YES' will purge the session. This action cannot be undone AND WILL KILL THE SESSION FOR ALL PARTICIPANTS! Use only after Session is finished for everybody!");
			
			if (userInput !== "YES") {
				console.log("[Client] Session end cancelled by user.");
				alert("Session end aborted. You must type 'YES' to confirm.");
				return;
			}
			try {
				const res = await fetchApi(`/api/session/${ this.sessionCode }/end`, { method: "POST" });
				if (!res.ok) {
					const errData = await res.json();
					alert("Failed to end session: " + (errData.error || "Unknown"));
					return;
				}
				alert("Session ended. All data purged.");
				window.location.reload();
			} catch (error) {
				console.error("[Client] Error in endSession():", error);
			}
		},

	}
}

Alpine.data("warmShowerApp", warmShowerApp)
Alpine.start()
